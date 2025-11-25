from celery import Celery
from app import create_app, config
import os

def create_celery_app(config_name=os.getenv('FLASK_ENV', 'default')):
    """
    Membuat instance Celery, terhubung ke konfigurasi Flask.
    """
    
    # 1. Buat aplikasi Flask sementara hanya untuk mendapatkan konfigurasinya
    flask_app = create_app(config_name)
    
    # 2. Buat instance Celery
    celery_app = Celery(
        __name__,
        broker=flask_app.config['CELERY_BROKER_URL'],
        backend=flask_app.config['CELERY_RESULT_BACKEND']
    )
    
    # 3. Sinkronkan konfigurasi Celery dari Flask
    celery_app.conf.update(flask_app.config)

    # 4. Buat "Task Context"
    # Ini memastikan task Celery berjalan di dalam "app context" Flask
    # Sehingga task Anda bisa mengakses 'db', 'app.config', dll.
    class ContextTask(celery_app.Task):
        def __call__(self, *args, **kwargs):
            with flask_app.app_context():
                return self.run(*args, **kwargs)

    celery_app.Task = ContextTask
    
    return celery_app

# --- Inisialisasi Global ---
# Ini adalah objek 'celery' yang akan diimpor oleh file lain
celery = create_celery_app()

# PENTING: Temukan dan daftarkan tasks.py
celery.autodiscover_tasks(['celery_worker'])