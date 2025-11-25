from flask import Blueprint

# 'api' adalah nama internal, karena ini akan melayani /api
analysis_bp = Blueprint('api', __name__)

# Kita akan buat file routes.py ini selanjutnya
from . import routes