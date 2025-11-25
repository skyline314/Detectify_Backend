from .extensions import db
from sqlalchemy.dialects.mysql import ENUM, JSON
from sqlalchemy import text
import uuid
import enum
from datetime import datetime

# 1. Definisikan Enum untuk Paket Langganan
class UserPlan(str, enum.Enum):
    FREE = 'FREE'
    PREMIUM = 'PREMIUM'

class User(db.Model):
    __tablename__ = 'Users'

    user_id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    
    # --- [BARU] Kolom Plan ---
    plan = db.Column(db.Enum(UserPlan), default=UserPlan.FREE, nullable=False)
    
    created_at = db.Column(db.TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = db.Column(db.TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'))

    # Relasi
    history = db.relationship('AnalysisHistory', backref='user', lazy=True, cascade="all, delete-orphan")

    # --- [BARU] Helper: Hitung Penggunaan Hari Ini ---
    def get_daily_usage_count(self):
        from .models import AnalysisHistory  # Import lokal untuk hindari circular import
        
        # Tentukan awal hari ini (jam 00:00:00)
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Hitung jumlah analisis user ini sejak jam 00:00 tadi
        count = AnalysisHistory.query.filter_by(user_id=self.user_id)\
            .filter(AnalysisHistory.created_at >= today_start)\
            .count()
            
        return count

    # --- [BARU] Helper: Cek Izin ---
    def can_analyze(self):
        # Jika Premium, bebas tanpa batas
        if self.plan == UserPlan.PREMIUM:
            return True
            
        # Jika Free, batasi (misal: 3 kali sehari)
        LIMIT_HARIAN = 3 
        return self.get_daily_usage_count() < LIMIT_HARIAN

    def __repr__(self):
        return f'<User {self.email} [{self.plan}]>'


class AnalysisHistory(db.Model):
    __tablename__ = 'AnalysisHistory'

    analysis_id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.String(36), db.ForeignKey('Users.user_id', ondelete='CASCADE'), nullable=False)
    
    status = db.Column(
        ENUM('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED', name='analysis_status_enum'), 
        nullable=False, 
        default='PENDING'
    )
    
    analysis_type = db.Column(
        ENUM('AUDIO', 'VIDEO', 'TEXT', 'IMAGE', name='analysis_type_enum'), 
        nullable=False
    )
    
    file_name_original = db.Column(db.String(255), nullable=True)
    file_location = db.Column(db.String(1024), nullable=False)
    result_summary = db.Column(JSON, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    
    created_at = db.Column(db.TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = db.Column(db.TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'))

    def __repr__(self):
        return f'<AnalysisHistory {self.analysis_id} [{self.status}]>'