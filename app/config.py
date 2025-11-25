import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BASE_DIR, '.env')
load_dotenv(dotenv_path=ENV_PATH)

class Config:
    """
    Konfigurasi dasar (Base) yang akan digunakan oleh semua lingkungan.
    Rahasia DIMUAT dari .env, BUKAN ditulis di sini.
    """

    # --- Flask & Keamanan ---
    SECRET_KEY = os.getenv('SECRET_KEY')
    DEBUG = False
    TESTING = False

    # --- JWT (Otentikasi) ---
    JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY')
    # Bisa tambahkan konfigurasi JWT lain di sini (misal: waktu kedaluwarsa token)

    # --- Database (SQLAlchemy) ---
    DB_USER = os.getenv('DB_USER')
    DB_PASS = os.getenv('DB_PASS')
    DB_HOST = os.getenv('DB_HOST')
    DB_NAME = os.getenv('DB_NAME')
    
    # Kita BANGUN connection string di sini dari variabel di atas
    SQLALCHEMY_DATABASE_URI = f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}/{DB_NAME}"
    
    # Nonaktifkan event tracking yang berisik dari SQLAlchemy
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- Celery (Async Tasks) ---
    CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL')
    CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND')

    # --- AWS S3 (Object Storage) ---
    AWS_S3_BUCKET_NAME = os.getenv('AWS_S3_BUCKET_NAME')
    AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
    AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')


class DevelopmentConfig(Config):
    """
    Konfigurasi khusus untuk Development.
    Mewarisi (inherits) dari Config dasar.
    """
    DEBUG = True
    # Anda bisa override DB di sini jika perlu, misal menunjuk ke DB lokal
    # SQLALCHEMY_DATABASE_URI = "sqlite:///dev.db" 


class ProductionConfig(Config):
    """
    Konfigurasi khusus untuk Production.
    Mewarisi (inherits) dari Config dasar.
    """
    DEBUG = False
    # Di production, Anda HARUS menggunakan variabel env yang berbeda
    # DB_USER = os.getenv('PROD_DB_USER')
    # ... dll


# --- Dictionary untuk memetakan string ke class Konfigurasi ---
# Ini akan digunakan oleh Application Factory (di __init__.py)
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}