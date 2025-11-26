# FILE: app/analysis/services.py
import uuid
import logging
from werkzeug.utils import secure_filename
from datetime import datetime
from flask import current_app

from app.extensions import db, s3_client
from app.models import AnalysisHistory, User

class AnalysisService:
    ALLOWED_EXTENSIONS = {'mp3', 'wav', 'm4a', 'flac', 'ogg'}

    @staticmethod
    def _validate_file(file):
        if not file or file.filename == '':
            raise ValueError("File tidak valid atau nama file kosong")
        
        filename = secure_filename(file.filename)
        if '.' not in filename:
            raise ValueError("File tidak memiliki ekstensi")
            
        ext = filename.rsplit('.', 1)[1].lower()
        if ext not in AnalysisService.ALLOWED_EXTENSIONS:
            raise ValueError(f"Format tidak didukung. Gunakan: {', '.join(AnalysisService.ALLOWED_EXTENSIONS)}")
        return filename, ext

    @staticmethod
    def process_upload(user_id, file):
        # 1. Cek User & Kuota
        user = User.query.filter_by(user_id=user_id).first()
        if not user:
            raise ValueError("User tidak ditemukan")

        if not user.can_analyze():
            raise PermissionError(f"Kuota habis. Terpakai: {user.get_daily_usage_count()}")

        # 2. Validasi & Upload S3
        original_filename, file_extension = AnalysisService._validate_file(file)
        bucket_name = current_app.config['AWS_S3_BUCKET_NAME']
        unique_id = str(uuid.uuid4())
        s3_file_key = f"audio/{user_id}/{unique_id}.{file_extension}"

        try:
            file.seek(0)
            s3_client.upload_fileobj(file, bucket_name, s3_file_key)
        except Exception as e:
            current_app.logger.error(f"S3 Upload Error: {e}")
            raise RuntimeError("Gagal upload ke storage cloud")

        # 3. DB Transaction
        job = AnalysisHistory(
            user_id=user_id,
            status='PENDING',
            analysis_type='AUDIO',
            file_name_original=original_filename,
            file_location=s3_file_key
        )
        
        try:
            db.session.add(job)
            db.session.commit()
            db.session.refresh(job)
        except Exception as e:
            db.session.rollback()
            s3_client.delete_object(Bucket=bucket_name, Key=s3_file_key)
            raise RuntimeError("Gagal menyimpan data transaksi")

        # 4. Dispatch Task
        try:
            from celery_worker.tasks import process_audio_task
            process_audio_task.apply_async(args=[job.analysis_id], queue='audio_queue')
        except ImportError:
             current_app.logger.warning("Celery task import failed")
        
        return {
            "message": "File diterima",
            "analysis_id": job.analysis_id,
            "status": "PENDING",
            "file_name": original_filename,
            "timestamp": datetime.utcnow().isoformat()
        }

    @staticmethod
    def get_user_history(user_id):
        """Mengambil semua riwayat user (Logic dipindah dari routes)"""
        history_list = AnalysisHistory.query.filter_by(user_id=user_id)\
            .order_by(AnalysisHistory.created_at.desc())\
            .all()

        results = []
        for item in history_list:
            results.append({
                "analysis_id": item.analysis_id,
                "status": item.status,
                "analysis_type": item.analysis_type,
                "file_name": item.file_name_original,
                "created_at": item.created_at.isoformat(),
                "result_summary": item.result_summary if item.status == 'COMPLETED' else None
            })
        return results

    @staticmethod
    def get_job_status(user_id, analysis_id):
        """Cek status satu job spesifik"""
        job = AnalysisHistory.query.filter_by(analysis_id=analysis_id, user_id=user_id).first()
        if not job:
            return None

        response = {
            "analysis_id": job.analysis_id,
            "status": job.status,
            "created_at": job.created_at.isoformat()
        }
        
        if job.status == 'COMPLETED':
            response["result"] = job.result_summary
        elif job.status == 'FAILED':
            response["error"] = job.error_message
            
        return response