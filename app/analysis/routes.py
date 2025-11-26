from flask import request, jsonify, current_app
from . import analysis_bp  # Mengimpor Blueprint 'analysis_bp' dari __init__.py paket ini
from app.models import AnalysisHistory, User
from app.extensions import db, s3_client
from flask_jwt_extended import jwt_required, get_jwt_identity
import uuid
from werkzeug.utils import secure_filename
from datetime import datetime

# CONFIG ROUTE 

@analysis_bp.before_request
@jwt_required()
def require_auth():
    """
    Firewall: Menerapkan @jwt_required() ke SETIAP rute 
    yang didefinisikan dalam Blueprint 'analysis_bp' ini.
    """
    pass

# ENDPOINTS 

@analysis_bp.route('/analysis/audio', methods=['POST'])
def upload_audio():
    """
    Endpoint untuk mengunggah file audio (LANGKAH 1 dari alur kerja).
    Mengkoordinasikan Cek Kuota, S3, DB, dan Celery.
    """
    
    # 1. Import Celery Task di dalam fungsi (LAZY IMPORT)
    # Ini PENTING untuk mencegah Flask memuat model ML yang berat saat startup
    # Model hanya akan dimuat oleh Worker di background, bukan oleh Flask.
    try:
        from celery_worker.tasks import process_audio_task
    except ImportError as e:
        current_app.logger.error(f"Gagal mengimpor task worker: {e}")
        return jsonify({"error": "Layanan pemrosesan sedang tidak tersedia (Worker Error)"}), 503

    # 2. Dapatkan User & Cek Kuota
    user_id = get_jwt_identity()
    user = User.query.filter_by(user_id=user_id).first()

    if not user:
        return jsonify({"error": "User tidak ditemukan"}), 404

    # CEK KUOTA 
    if not user.can_analyze():
        usage_count = user.get_daily_usage_count()
        return jsonify({
            "error": "Kuota harian habis",
            "message": f"Anda telah menggunakan {usage_count} dari batas kuota harian. Silakan upgrade ke Premium.",
            "current_usage": usage_count
        }), 403 # Forbidden

    # 3. Validasi File
    if 'file' not in request.files:
        return jsonify({"error": "Tidak ada file yang dikirim"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "Nama file kosong"}), 400

    # Validasi ekstensi file
    allowed_extensions = {'mp3', 'wav', 'm4a', 'flac', 'ogg'}
    original_filename = secure_filename(file.filename)
    
    if '.' not in original_filename:
        return jsonify({"error": "File tidak memiliki ekstensi"}), 400
        
    file_extension = original_filename.rsplit('.', 1)[1].lower()
    
    if file_extension not in allowed_extensions:
        return jsonify({"error": f"Format file tidak didukung. Gunakan: {', '.join(allowed_extensions)}"}), 400

    # 4. Persiapan Upload S3
    bucket_name = current_app.config['AWS_S3_BUCKET_NAME']
    
    # Buat nama file unik: audio/<user_id>/<uuid>.<ext>
    unique_id = str(uuid.uuid4())
    s3_file_key = f"audio/{user_id}/{unique_id}.{file_extension}"

    # 5. Upload ke S3
    try:
        # Reset pointer file ke awal (jaga-jaga)
        file.seek(0)
        
        s3_client.upload_fileobj(
            file,           # Objek file stream dari Flask
            bucket_name,    # Nama Bucket
            s3_file_key     # Key (Path) di S3
        )
    except Exception as e:
        current_app.logger.error(f"Gagal upload ke S3: {e}")
        return jsonify({"error": "Gagal mengunggah file ke cloud storage"}), 500

    # 6. Buat Entri Database (Tiket Pekerjaan)
    new_job = None
    try:
        new_job = AnalysisHistory(
            user_id=user_id,
            status='PENDING',
            analysis_type='AUDIO', # Hardcode untuk endpoint audio
            file_name_original=original_filename,
            file_location=s3_file_key # Simpan path S3
        )
        db.session.add(new_job)
        db.session.commit()
        
        # Refresh untuk mendapatkan ID dan waktu yang digenerate DB
        db.session.refresh(new_job) 
        analysis_id = new_job.analysis_id
    
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Gagal insert DB: {e}")
        
        # Kompensasi: Hapus file yang sudah terlanjur di-upload ke S3
        try:
            s3_client.delete_object(Bucket=bucket_name, Key=s3_file_key)
        except:
            pass # Abaikan error cleanup
            
        return jsonify({"error": "Gagal membuat tiket analisis di database"}), 500

    # 7. Kirim Tugas ke Celery (Pabrik)
    try:
        process_audio_task.apply_async(
            args=[analysis_id], # Kirim ID pekerjaan
            queue='audio_queue' # Routing ke antrian audio
        )
    except Exception as e:
        current_app.logger.error(f"Gagal mengirim ke Celery: {e}")
        
        # Rollback Database & S3 jika gagal kirim tugas
        db.session.delete(new_job)
        db.session.commit()
        try:
            s3_client.delete_object(Bucket=bucket_name, Key=s3_file_key)
        except:
            pass
            
        return jsonify({"error": "Gagal mengirim pekerjaan ke antrian pemrosesan"}), 500

    # 8. Berhasil (Kembalikan Tiket)
    return jsonify({
        "message": "File diterima dan sedang diproses",
        "analysis_id": analysis_id,
        "status": "PENDING",
        "file_name": original_filename,
        "timestamp": datetime.utcnow().isoformat()
    }), 202 # 202 Accepted


@analysis_bp.route('/history', methods=['GET'])
def get_history():
    """
    Endpoint untuk mengambil semua riwayat analisis pengguna ini.
    """
    user_id = get_jwt_identity()

    # Ambil dari DB, urutkan dari yang terbaru
    history_list = AnalysisHistory.query.filter_by(user_id=user_id)\
        .order_by(AnalysisHistory.created_at.desc())\
        .all()

    results = []
    for item in history_list:
        # Format hasil agar rapi
        result_data = {
            "analysis_id": item.analysis_id,
            "status": item.status,
            "analysis_type": item.analysis_type,
            "file_name": item.file_name_original,
            "created_at": item.created_at.isoformat(),
            # Sertakan hasil ringkas jika sudah selesai
            "result_summary": item.result_summary if item.status == 'COMPLETED' else None
        }
        results.append(result_data)

    return jsonify(results), 200


@analysis_bp.route('/analysis/<string:analysis_id>', methods=['GET'])
def get_analysis_status(analysis_id):
    """
    Endpoint untuk polling status satu pekerjaan analisis spesifik.
    """
    user_id = get_jwt_identity()
    
    # Query berdasarkan ID DAN User ID (Keamanan: User A gak boleh intip User B)
    job = AnalysisHistory.query.filter_by(analysis_id=analysis_id, user_id=user_id).first()

    if not job:
        return jsonify({"error": "Analisis tidak ditemukan atau akses ditolak"}), 404

    response = {
        "analysis_id": job.analysis_id,
        "status": job.status,
        "created_at": job.created_at.isoformat()
    }

    # Tambahkan detail sesuai status
    if job.status == 'COMPLETED':
        response["result"] = job.result_summary
    elif job.status == 'FAILED':
        response["error"] = job.error_message
    
    return jsonify(response), 200