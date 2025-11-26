from flask import Blueprint
# 'api' adalah nama internal, karena ini akan melayani /api
analysis_bp = Blueprint('api', __name__)
from . import routes