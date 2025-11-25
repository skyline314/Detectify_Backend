import os
from app import create_app

# Muat konfigurasi dari env variable 'FLASK_ENV' (production/development)
config_name = os.getenv('FLASK_ENV', 'default')

# Panggil factory untuk membuat aplikasi
app = create_app(config_name)

if __name__ == '__main__':
    app.run()