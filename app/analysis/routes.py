from flask import request, jsonify, current_app
from . import analysis_bp  # Impor Blueprint
from app.models import AnalysisHistory, User
from app.extensions import db, s3_client # <-- Pastikan s3_client ada di sini
from flask_jwt_extended import jwt_required, get_jwt_identity
import uuid # <-- Impor baru untuk ID unik
from werkzeug.utils import secure_filename # <-- Impor baru untuk keamanan nama file
from app.models import AnalysisHistory, User

# --- Impor Task Celery ---
# Ini adalah 'jembatan' ke 'Pabrik' Anda
try:
    from celery_worker.tasks import process_audio_task
except ImportError:
    # Ini akan gagal jika Celery belum terinstal, tapi kita tangani
    process_audio_task = None

# --- Firewall Otentikasi (Sudah ada) ---
@analysis_bp.before_request
@jwt_required()
def require_auth():
    """
    Menerapkan @jwt_required() ke SETIAP rute 
    yang didefinisikan dalam Blueprint 'analysis_bp' ini.
    """
    pass

# --- INI ADALAH FUNGSI YANG KITA UPDATE ---
@analysis_bp.route('/analysis/audio', methods=['POST'])
def upload_audio():
    """
    Endpoint untuk mengunggah file audio (LANGKAH 1 dari alur kerja).
    Mengkoordinasikan S3, DB, dan Celery.
    """
    
    # 0. Cek apakah Pabrik Celery terhubung
    if process_audio_task is None:
        return jsonify({"error": "Layanan pemrosesan (worker) tidak terkonfigurasi"}), 503 # Service Unavailable
        
    # 1. Dapatkan User
    user_id = get_jwt_identity()
    user = User.query.filter_by(user_id=user_id).first()

    if not user:
        return jsonify({"error": "User tidak ditemukan"}), 404

    # --- [LOGIKA BARU] CEK KUOTA ---
    # Cek apakah user boleh melakukan analisis lagi
    if not user.can_analyze():
        usage_count = user.get_daily_usage_count()
        return jsonify({
            "error": "Kuota harian habis",
            "message": f"Anda telah menggunakan {usage_count} dari 3 kuota harian.",
            "upgrade_url": "/upgrade-premium" # (Placeholder untuk nanti)
        }), 403 # 403 Forbidden
    
    # 2. Validasi File (Lanjut seperti biasa...)
    if 'file' not in request.files:
        return jsonify({"error": "Tidak ada file"}), 400    
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "File tidak dipilih"}), 400

    # Validasi ekstensi file (contoh sederhana)
    allowed_extensions = {'mp3', 'wav', 'm4a'}
    original_filename = secure_filename(file.filename)
    
    if '.' not in original_filename or original_filename.rsplit('.', 1)[1].lower() not in allowed_extensions:
        return jsonify({"error": "Format file tidak didukung (hanya mp3, wav, m4a)"}), 400

    # 3. Persiapan Upload S3
    bucket_name = current_app.config['AWS_S3_BUCKET_NAME']
    file_extension = original_filename.rsplit('.', 1)[1].lower()
    
    # Buat nama file unik untuk S3 agar tidak bertabrakan
    # Format: audio/<user_id>/<uuid_unik>.<ekstensi>
    s3_file_key = f"audio/{user_id}/{uuid.uuid4()}.{file_extension}"

    # 4. Upload ke S3
    try:
        s3_client.upload_fileobj(
            file,           # Objek file stream dari Flask
            bucket_name,    # Nama Bucket S3 Anda
            s3_file_key     # Nama/path file unik di S3
        )
    except Exception as e:
        current_app.logger.error(f"Gagal upload S3: {e}")
        return jsonify({"error": "Gagal mengunggah file ke storage"}), 500

    # 5. Buat Entri Database (Tiket Pekerjaan)
    new_job = None
    try:
        new_job = AnalysisHistory(
            user_id=user_id,
            status='PENDING',
            analysis_type='AUDIO', # <-- Hardcode untuk endpoint ini
            file_name_original=original_filename,
            file_location=s3_file_key # <-- Simpan path S3, BUKAN URL
        )
        db.session.add(new_job)
        db.session.commit()
        db.session.refresh(new_job) # Ambil ID yang baru dibuat
        analysis_id = new_job.analysis_id
    
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Gagal insert DB: {e}")
        # Gagal simpan DB? Hapus file "yatim" di S3 agar storage tidak penuh
        s3_client.delete_object(Bucket=bucket_name, Key=s3_file_key)
        return jsonify({"error": "Gagal membuat entri database"}), 500

    # 6. Kirim Tugas ke Celery (Pabrik)
    try:
        process_audio_task.apply_async(
            args=[analysis_id], # Kirim ID pekerjaan
            queue='audio_queue' # (Praktik baik untuk routing)
        )
    except Exception as e:
        current_app.logger.error(f"Gagal antri Celery: {e}")
        # Gagal kirim ke antrian? Ini bencana. Rollback semuanya.
        db.session.delete(new_job)
        db.session.commit()
        s3_client.delete_object(Bucket=bucket_name, Key=s3_file_key)
        return jsonify({"error": "Gagal mengirim pekerjaan ke antrian pemrosesan"}), 500

    # 7. Berhasil (Kembalikan Tiket)
    return jsonify({
        "message": "File diterima dan sedang diproses",
        "analysis_id": analysis_id,
        "status": "PENDING"
    }), 202 # 202 Accepted (PENTING: Menandakan proses asinkron)


# --- FUNGSI-FUNGSI DI BAWAH INI TETAP SAMA (TIDAK BERUBAH) ---

@analysis_bp.route('/history', methods=['GET'])
def get_history():
    """
    Endpoint untuk mengambil riwayat analisis pengguna.
    """
    user_id = get_jwt_identity()
    history_list = AnalysisHistory.query.filter_by(user_id=user_id).order_by(AnalysisHistory.created_at.desc()).all()
    results = [
        {
            "analysis_id": item.analysis_id,
            "status": item.status,
            "analysis_type": item.analysis_type,
            "file_name": item.file_name_original,
            "created_at": item.created_at.isoformat()
        } for item in history_list
    ]
    return jsonify(results), 200


@analysis_bp.route('/analysis/<string:analysis_id>', methods=['GET'])
def get_analysis_status(analysis_id):
    """
    Endpoint untuk polling status satu pekerjaan analisis.
    """
    user_id = get_jwt_identity()
    job = AnalysisHistory.query.filter_by(analysis_id=analysis_id, user_id=user_id).first()

    if not job:
        return jsonify({"error": "Analisis tidak ditemukan"}), 404

    return jsonify({
        "analysis_id": job.analysis_id,
        "status": job.status,
        "result": job.result_summary,
        "error": job.error_message,
        "created_at": job.created_at.isoformat()
    }), 200