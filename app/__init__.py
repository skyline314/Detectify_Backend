from flask import Flask
from .config import config
from .extensions import (
    db, 
    migrate, 
    jwt, 
    cors, 
    init_s3_client
)

def create_app(config_name='default'):
    
    app = Flask(__name__)
    app.config.from_object(config[config_name])

    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    cors.init_app(app)
    init_s3_client(app)

    with app.app_context():
        from . import models 
        
    # 1. Blueprint Auth
    from .auth import auth_bp
    app.register_blueprint(auth_bp, url_prefix='/auth')

    # 2. Blueprint Analysis
    from .analysis import analysis_bp
    app.register_blueprint(analysis_bp, url_prefix='/api')

    @app.route('/hello')
    def hello():
        return "Hello, World! Factory is working."

    return app