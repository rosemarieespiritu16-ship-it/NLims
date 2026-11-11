import os
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
dotenv_path = os.path.join(os.path.dirname(basedir), '.env')
load_dotenv(dotenv_path)


class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-change-me')
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'mysql+pymysql://root:@localhost/inventorpro')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    APP_URL = os.getenv('APP_URL', 'http://localhost:5000')
    APP_NAME = os.getenv('APP_NAME', 'InventorPro')

    MAIL_SERVER = os.getenv('SMTP_HOST', 'smtp.gmail.com')
    MAIL_PORT = int(os.getenv('SMTP_PORT', 587))
    MAIL_USE_TLS = True
    MAIL_USERNAME = os.getenv('SMTP_USER')
    MAIL_PASSWORD = os.getenv('SMTP_PASS')
    MAIL_DEFAULT_SENDER = os.getenv('SMTP_FROM', os.getenv('SMTP_USER'))

    PUSHER_APP_ID = os.getenv('PUSHER_APP_ID')
    PUSHER_KEY = os.getenv('PUSHER_KEY')
    PUSHER_SECRET = os.getenv('PUSHER_SECRET')
    PUSHER_CLUSTER = os.getenv('PUSHER_CLUSTER', 'ap1')

    PAYMONGO_PUBLIC_KEY = os.getenv('PAYMONGO_PUBLIC_KEY')
    PAYMONGO_SECRET_KEY = os.getenv('PAYMONGO_SECRET_KEY')
