import os
import joblib
import pandas as pd
import numpy as np
import librosa
import io
import json
from .celery_app import celery
from app.models import AnalysisHistory
from app.extensions import db, s3_client
from flask import current_app

# =====================================================================
# 1. KONFIGURASI PATH & KONSTANTA
# =====================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, 'assets')
MODEL_DIR = os.path.join(ASSETS_DIR, 'models')
FEATURE_LIST_FILE = os.path.join(ASSETS_DIR, 'selected_features.csv') 

SR = 16000
N_MFCC = 39 

# Daftar Path Model (Bisa ditambah tanpa merusak logic utama)
MODELS_PATHS = {
    'SVM': os.path.join(MODEL_DIR, 'SVM', 'svm_detektor.pkl'),
    'XGBoost': os.path.join(MODEL_DIR, 'XGBoost', 'xgboost_detektor.pkl'),
    # 'RandomForest': os.path.join(MODEL_DIR, 'RandomForest', 'rf_detektor.pkl'), # Contoh extensi
}

SCALERS_PATHS = {
    'SVM': os.path.join(MODEL_DIR, 'SVM', 'scaler_svm.pkl'),
}

# =====================================================================
# 2. MODEL REGISTRY (Strategy Pattern Implementation)
# =====================================================================

class ModelRegistry:
    """
    Kelas tunggal untuk mengelola pemuatan aset ML dan prediksi.
    Menerapkan Lazy Loading agar hemat memori saat idle.
    """
    def __init__(self):
        self.models = {}
        self.scalers = {}
        self.feature_cols = None
        self._is_loaded = False

    def load_assets(self):
        """Memuat semua model dan scaler ke memori hanya jika belum dimuat."""
        if self._is_loaded:
            return
        
        print("[Worker] Loading ML Models into Memory...")
        try:
            # 1. Load Feature Columns
            if os.path.exists(FEATURE_LIST_FILE):
                self.feature_cols = pd.read_csv(FEATURE_LIST_FILE).drop(columns=['file_name', 'label'], errors='ignore').columns.tolist()
            else:
                print(f"[Worker] Warning: Feature list file not found at {FEATURE_LIST_FILE}")
                self.feature_cols = []

            # 2. Load Models
            for name, path in MODELS_PATHS.items():
                if os.path.exists(path):
                    self.models[name] = joblib.load(path)
                else:
                    print(f"[Worker] Warning: Model {name} not found at {path}")

            # 3. Load Scalers
            for name, path in SCALERS_PATHS.items():
                if os.path.exists(path):
                    self.scalers[name] = joblib.load(path)
            
            self._is_loaded = True
            print("[Worker] Assets Loaded Successfully.")
            
        except Exception as e:
            print(f"[Worker] FATAL ERROR loading assets: {e}")
            raise RuntimeError("Gagal memuat aset Machine Learning")

    def predict(self, model_name, features_dict):
        """
        Melakukan prediksi menggunakan model spesifik.
        Otomatis menangani scaling dan formatting output.
        """
        # Pastikan aset termuat
        if not self._is_loaded:
            self.load_assets()

        # Fallback: Jika model yang diminta tidak ada, pakai yang tersedia pertama
        if model_name not in self.models:
            if not self.models:
                raise RuntimeError("Tidak ada model ML yang tersedia di registry.")
            model_name = list(self.models.keys())[0]

        model = self.models[model_name]
        
        # Persiapan DataFrame
        df_input = pd.DataFrame([features_dict])
        
        # Safety: Pastikan kolom fitur lengkap (isi 0 jika hilang)
        if self.feature_cols:
            for col in self.feature_cols:
                if col not in df_input.columns:
                    df_input[col] = 0
            # Reorder kolom sesuai training
            X = df_input[self.feature_cols]
        else:
            X = df_input # Fallback jika feature list gagal load

        # Scaling
        if model_name in self.scalers:
            X = self.scalers[model_name].transform(X)
        else:
            X = X.values

        # Inference
        try:
            prediction = model.predict(X)[0]
            # Cek apakah model support probabilitas
            if hasattr(model, "predict_proba"):
                proba = model.predict_proba(X)[0]
                prob_fake = float(proba[1])
                prob_real = float(proba[0])
            else:
                # Fallback untuk model tanpa proba (misal SVM linear tertentu)
                prob_fake = 1.0 if prediction == 1 else 0.0
                prob_real = 1.0 - prob_fake

            return {
                "model_used": model_name,
                "prediction": 'FAKE' if prediction == 1 else 'REAL',
                "probability_fake": prob_fake,
                "probability_real": prob_real,
                "confidence_score": float(max(prob_fake, prob_real))
            }
        except Exception as e:
            raise RuntimeError(f"Error saat inferensi model {model_name}: {e}")

# Inisialisasi Global Registry
ml_registry = ModelRegistry()

# =====================================================================
# 3. FUNGSI EKSTRAKSI FITUR (Librosa Helper)
# =====================================================================

def extract_single_feature(audio_buffer):
    """
    Ekstrak fitur MFCC dan statistik spektral dari buffer audio.
    """
    try:
        audio_buffer.seek(0)
        y, sr = librosa.load(audio_buffer, sr=SR)
    except Exception as e:
        print(f"[Worker] Error decoding audio: {e}")
        return None

    features = {}
    
    # 1. MFCC Extraction
    mfccs = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13) 
    mfcc_base = mfccs[:13, :]
    mfcc_delta = librosa.feature.delta(mfcc_base)
    mfcc_delta2 = librosa.feature.delta(mfcc_delta)
    full_mfccs = np.concatenate((mfcc_base, mfcc_delta, mfcc_delta2), axis=0) 
    
    # Rata-rata setiap koefisien MFCC
    for i in range(N_MFCC):
        if i < full_mfccs.shape[0]:
            features[f'mfcc_{i+1}'] = np.mean(full_mfccs[i])
        else:
             features[f'mfcc_{i+1}'] = 0

    # 2. Fitur Tambahan (Spectral & Temporal)
    try:
        features['zcr_mean'] = np.mean(librosa.feature.zero_crossing_rate(y))
        features['spectral_centroid_mean'] = np.mean(librosa.feature.spectral_centroid(y=y, sr=sr))
        features['spectral_rolloff_mean'] = np.mean(librosa.feature.spectral_rolloff(y=y, sr=sr))
        features['spectral_contrast_mean'] = np.mean(librosa.feature.spectral_contrast(y=y, sr=sr))
        features['zcr_std'] = np.std(librosa.feature.zero_crossing_rate(y))
        features['spectral_centroid_std'] = np.std(librosa.feature.spectral_centroid(y=y, sr=sr))
    except Exception as e:
        print(f"[Worker] Warning extracting spectral features: {e}")

    return features

# =====================================================================
# 4. CELERY TASKS (Business Logic Execution)
# =====================================================================

@celery.task(name='process_audio_task')
def process_audio_task(analysis_id):
    """
    Worker utama. Menerima ID, mengambil data, memproses via Registry, simpan hasil.
    """
    print(f"[Worker] Starting Job: {analysis_id}")
    
    # 1. Ambil Job Record dari DB
    job = AnalysisHistory.query.filter_by(analysis_id=analysis_id).first()
    if not job:
        print(f"[Worker] Error: Job ID {analysis_id} not found in DB.")
        return

    try:
        # Update Status -> PROCESSING
        job.status = 'PROCESSING'
        db.session.commit()

        # 2. Ambil File dari S3
        bucket_name = current_app.config['AWS_S3_BUCKET_NAME']
        file_key = job.file_location
        
        print(f"[Worker] Fetching from S3: {file_key}")
        s3_response = s3_client.get_object(Bucket=bucket_name, Key=file_key)
        audio_data_bytes = s3_response['Body'].read()
        audio_buffer = io.BytesIO(audio_data_bytes)

        # 3. Ekstrak Fitur
        print("[Worker] Extracting features...")
        features_dict = extract_single_feature(audio_buffer)
        if features_dict is None:
            raise ValueError("Gagal mengekstrak fitur audio (File corrupt atau format tidak didukung librosa)")

        # 4. Prediksi (Menggunakan Registry)
        # Di sini kita bisa pilih model secara dinamis.
        # Untuk sekarang default ke XGBoost, tapi logic ini 'closed' dari perubahan internal registry.
        print("[Worker] Running Inference...")
        result_data = ml_registry.predict('XGBoost', features_dict)

        # 5. Simpan Hasil
        job.status = 'COMPLETED'
        job.result_summary = result_data
        db.session.commit()
        print(f"[Worker] Job {analysis_id} COMPLETED. Result: {result_data['prediction']}")

        # 6. Cleanup (Opsional: Hapus file dari S3 untuk hemat biaya)
        try:
            s3_client.delete_object(Bucket=bucket_name, Key=file_key)
            print("[Worker] S3 Cleanup done.")
        except Exception as cleanup_error:
            print(f"[Worker] Warning S3 Cleanup: {cleanup_error}")

    except Exception as e:
        print(f"[Worker] Job Failed: {e}")
        db.session.rollback()
        
        # Update Status -> FAILED
        job.status = 'FAILED'
        job.error_message = str(e)
        db.session.commit()