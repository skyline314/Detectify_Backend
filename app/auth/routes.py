from flask import request, jsonify
from . import auth_bp
from app.models import User
from app.extensions import db
import bcrypt
from flask_jwt_extended import create_access_token
@auth_bp.route('/register', methods=['POST'])
def register_user():
    """
    Endpoint untuk Pendaftaran Pengguna Baru
    Menerima: JSON { "email": "...", "password": "..." }
    """
    try:
        data = request.get_json()
        if not data or not data.get('email') or not data.get('password'):
            return jsonify({"error": "Email dan password diperlukan"}), 400

        email = data.get('email')
        password = data.get('password')

        # validasi 1: cek apakah email sudah ada 
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            return jsonify({"error": "Email ini sudah terdaftar"}), 409 # 409 Conflict

        # proses: buat hash password
        password_bytes = password.encode('utf-8')
        salt = bcrypt.gensalt()
        password_hash = bcrypt.hashpw(password_bytes, salt)
        password_hash_str = password_hash.decode('utf-8')

        # simpan ke database
        new_user = User(
            email=email,
            password_hash=password_hash_str
        )
        
        db.session.add(new_user)
        db.session.commit()

        return jsonify({"message": "Pengguna berhasil dibuat"}), 201 # 201 Created

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Terjadi kesalahan internal", "details": str(e)}), 500



@auth_bp.route('/login', methods=['POST'])
def login_user():
    """
    Endpoint untuk Login Pengguna
    Menerima: JSON { "email": "...", "password": "..." }
    Mengembalikan: JSON { "access_token": "..." }
    """
    try:
        data = request.get_json()
        if not data or not data.get('email') or not data.get('password'):
            return jsonify({"error": "Email dan password diperlukan"}), 400

        email = data.get('email')
        password = data.get('password')

        # validasi 1: cari pengguna berdasarkan email
        user = User.query.filter_by(email=email).first()

        if not user:
            return jsonify({"error": "Email atau password salah"}), 401 # 401 Unauthorized

        # validasi 2: cek hash password 
        password_bytes = password.encode('utf-8')
        password_hash_bytes = user.password_hash.encode('utf-8')

        if not bcrypt.checkpw(password_bytes, password_hash_bytes):
            return jsonify({"error": "Email atau password salah"}), 401 # 401 Unauthorized

        # sukses: buat token JWT 
        # 'identity' kita gunakan user_id karena unik
        access_token = create_access_token(identity=user.user_id)
        
        return jsonify(access_token=access_token), 200

    except Exception as e:
        return jsonify({"error": "Terjadi kesalahan internal", "details": str(e)}), 500