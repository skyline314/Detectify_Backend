# app/analysis/routes.py
from flask import request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from . import analysis_bp
from .services import AnalysisService 

# --- 1. ENDPOINT UPLOAD ---
@analysis_bp.route('/analysis/audio', methods=['POST'])
@jwt_required()
def upload_audio():
    if 'file' not in request.files:
        return jsonify({"error": "Tidak ada file yang dikirim"}), 400
        
    file = request.files['file']
    user_id = get_jwt_identity()

    try:
        result = AnalysisService.process_upload(user_id, file)
        return jsonify(result), 202
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        return jsonify({"error": "Internal Error", "details": str(e)}), 500

# --- 2. ENDPOINT HISTORY (Pastikan ini ada!) ---
@analysis_bp.route('/history', methods=['GET'])
@jwt_required()
def get_history():
    user_id = get_jwt_identity()
    try:
        results = AnalysisService.get_user_history(user_id)
        return jsonify(results), 200
    except Exception as e:
        return jsonify({"error": "Gagal mengambil riwayat", "details": str(e)}), 500

# --- 3. ENDPOINT STATUS (Pastikan ini ada!) ---
@analysis_bp.route('/analysis/<string:analysis_id>', methods=['GET'])
@jwt_required()
def get_analysis_status(analysis_id):
    user_id = get_jwt_identity()
    try:
        result = AnalysisService.get_job_status(user_id, analysis_id)
        if not result:
            return jsonify({"error": "Tidak ditemukan"}), 404
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": "Gagal cek status", "details": str(e)}), 500