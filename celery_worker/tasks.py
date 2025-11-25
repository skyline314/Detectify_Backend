import os
import joblib
import pandas as pd
import numpy as np
import librosa
import io
import json
from .celery_app import celery  # Impor instance Celery yang kita buat
from app.models import AnalysisHistory
from app.extensions import db, s3_client
from flask import current_app

# =====================================================================
# 1. KONFIGURASI PATH & ML (Disalin dari app.py Anda)
# =====================================================================

# Path sekarang relatif terhadap file 'tasks.py' ini
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, 'assets')
MODEL_DIR = os.path.join(ASSETS_DIR, 'models')
FEATURE_LIST_FILE = os.path.join(ASSETS_DIR, 'selected_features.csv') 

# PARAMETER EKSTRAKSI (Wajib Konsisten!)
SR = 16000
N_MFCC = 39 

# Daftar Model dan Path
MODELS = {
    'SVM': os.path.join(MODEL_DIR, 'SVM', 'svm_detektor.pkl'),
    'XGBoost': os.path.join(MODEL_DIR, 'XGBoost', 'xgboost_detektor.pkl')
    # Tambahkan model lain jika perlu
}
SCALERS = {
    'SVM': os.path.join(MODEL_DIR, 'SVM', 'scaler_svm.pkl'),
}

# =====================================================================
# 2. MUAT ASET (Hanya sekali saat worker dimulai)
# =====================================================================
try:
    ALL_MODELS = {name: joblib.load(path) for name, path in MODELS.items()}
    ALL_SCALERS = {name: joblib.load(path) for name, path in SCALERS.items()}
    FEATURE_COLS = pd.read_csv(FEATURE_LIST_FILE).drop(columns=['file_name', 'label']).columns.tolist()
except Exception as e:
    # Jika worker gagal memuat model, ini adalah error fatal
    print(f"FATAL ERROR: Gagal memuat aset ML. Worker tidak akan berfungsi. Error: {e}")
    ALL_MODELS, ALL_SCALERS, FEATURE_COLS = {}, {}, []

# =====================================================================
# 3. FUNGSI EKSTRAKSI FITUR (Disalin dari app.py Anda)
# =====================================================================

def extract_single_feature(audio_buffer):
    """Ekstrak fitur lengkap dari data audio mentah (bytes)."""
    try:
        # 'audio_buffer' adalah io.BytesIO, bukan file upload
        audio_buffer.seek(0)
        y, sr = librosa.load(audio_buffer, sr=SR)
    except Exception as e:
        print(f"Error memproses audio: {e}")
        return None

    features = {}
    
    # Ekstraksi MFCC
    mfccs = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13) 
    mfcc_base = mfccs[:13, :]
    mfcc_delta = librosa.feature.delta(mfcc_base)
    mfcc_delta2 = librosa.feature.delta(mfcc_delta)
    full_mfccs = np.concatenate((mfcc_base, mfcc_delta, mfcc_delta2), axis=0) 
    
    for i in range(N_MFCC):
        features[f'mfcc_{i+1}'] = np.mean(full_mfccs[i])

    # Fitur Spektral dan Temporal Lain
    features['zcr_mean'] = np.mean(librosa.feature.zero_crossing_rate(y))
    features['spectral_centroid_mean'] = np.mean(librosa.feature.spectral_centroid(y=y, sr=sr))
    features['spectral_rolloff_mean'] = np.mean(librosa.feature.spectral_rolloff(y=y, sr=sr))
    features['spectral_contrast_mean'] = np.mean(librosa.feature.spectral_contrast(y=y, sr=sr))
    features['zcr_std'] = np.std(librosa.feature.zero_crossing_rate(y))
    features['spectral_centroid_std'] = np.std(librosa.feature.spectral_centroid(y=y, sr=sr))
    
    return features

# =====================================================================
# 4. TUGAS CELERY UTAMA
# =====================================================================

@celery.task(name='process_audio_task')
def process_audio_task(analysis_id):
    """
    Tugas asinkron untuk memproses file audio.
    Ini adalah "Pabrik" Anda.
    """
    
    # 1. Dapatkan "Pekerjaan" dari Database
    job = AnalysisHistory.query.filter_by(analysis_id=analysis_id).first()
    if not job:
        print(f"Error: Job {analysis_id} tidak ditemukan.")
        return

    try:
        # 2. Update status ke PROCESSING
        job.status = 'PROCESSING'
        db.session.commit()

        # 3. Unduh File dari S3
        bucket_name = current_app.config['AWS_S3_BUCKET_NAME']
        file_key = job.file_location # file_location akan kita isi dengan key S3
        
        # Unduh file sebagai objek 'bytes' di memori
        s3_response = s3_client.get_object(Bucket=bucket_name, Key=file_key)
        audio_data_bytes = s3_response['Body'].read()
        audio_buffer = io.BytesIO(audio_data_bytes)
        
        # 4. Ekstrak Fitur (menggunakan fungsi di atas)
        features_dict = extract_single_feature(audio_buffer)
        if features_dict is None:
            raise Exception("Gagal mengekstrak fitur audio.")

        # 5. Pilih Model & Prediksi (menggunakan aset yang sudah dimuat)
        model_name = 'SVM' # Hardcode ke SVM untuk saat ini
        
        df_new_all = pd.DataFrame([features_dict])
        X_new = df_new_all[FEATURE_COLS] # Pastikan urutan fitur benar

        model = ALL_MODELS[model_name]
        
        if model_name in ALL_SCALERS:
            scaler = ALL_SCALERS[model_name]
            X_input = scaler.transform(X_new)
        else:
            X_input = X_new.values
        
        prediction = model.predict(X_input)[0]
        proba = model.predict_proba(X_input)[0]
        
        result_label = 'FAKE' if prediction == 1 else 'REAL'
        prob_fake = float(proba[1]) # float() penting untuk serialisasi JSON
        prob_real = float(proba[0])

        # 6. Simpan Hasil ke Database
        job.status = 'COMPLETED'
        job.result_summary = {
            "model_used": model_name,
            "prediction": result_label,
            "probability_fake": prob_fake,
            "probability_real": prob_real
        }
        db.session.commit()
        
        # 7. Hapus File dari S3 (Pembersihan)
        s3_client.delete_object(Bucket=bucket_name, Key=file_key)

    except Exception as e:
        # Tangani Error
        db.session.rollback()
        job.status = 'FAILED'
        job.error_message = str(e)
        db.session.commit()
        print(f"Error processing job {analysis_id}: {e}")