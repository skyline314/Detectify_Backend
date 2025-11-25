from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from flask_migrate import Migrate
from boto3 import Session
from botocore.config import Config
import os

# 1. Inisialisasi Database (SQLAlchemy) & Migrasi
# Kita belum mengikatnya ke aplikasi (app) di sini.
db = SQLAlchemy()
migrate = Migrate()

# 2. Inisialisasi JWT (Otentikasi)
jwt = JWTManager()

# 3. Inisialisasi CORS (Cross-Origin Resource Sharing)
# Ini WAJIB agar [FE] Anda (React/Vue/dll) bisa memanggil [BE] Anda
cors = CORS()

# 4. Inisialisasi Sesi Boto3 (AWS S3)
# Ini sedikit berbeda, kita akan buat 'klien' S3 yang bisa di-reuse
# Ini akan membaca kredensial dari config yang kita buat sebelumnya
s3_client = None

def init_s3_client(app):
    """
    Fungsi helper untuk menginisialisasi S3 client setelah app dibuat,
    karena kita butuh config dari app.
    """
    global s3_client
    
    # Konfigurasi untuk S3, misal region
    # Anda mungkin perlu menambahkan AWS_REGION ke file .env dan config.py Anda
    # s3_config = Config(
    #     region_name=app.config.get('AWS_S3_REGION', 'ap-southeast-1') 
    # )

    session = Session(
        aws_access_key_id=app.config['AWS_ACCESS_KEY_ID'],
        aws_secret_access_key=app.config['AWS_SECRET_ACCESS_KEY']
        # config=s3_config  # Aktifkan jika Anda butuh region
    )
    
    s3_client = session.client('s3')
    
    # Untuk S3, kita mungkin juga butuh nama bucket di seluruh aplikasi
    app.config['S3_CLIENT'] = s3_client