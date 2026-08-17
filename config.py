import os
from dotenv import load_dotenv

load_dotenv()
db_file = os.getenv('DATABASE_URL', 'sqlite:///database/users.db')
BOT_TOKEN = os.getenv('BOT_TOKEN')
admin_id_str = os.getenv('ADMIN_IDS', '')
ADMIN_IDS = [int(i.strip()) for i in admin_id_str.split(',') if i.strip()]
secret_key = os.getenv('SECRET_KEY')
fastapi_key = os.getenv('API_STATS_KEY')
YOOKASSA_WEBHOOK_SECRET = os.getenv('YOOKASSA_WEBHOOK_SECRET')
SUPER_YOOKASSA_SHOP_ID = os.getenv("SUPER_YOOKASSA_SHOP_ID", "").strip()
SUPER_YOOKASSA_SECRET_KEY = os.getenv("SUPER_YOOKASSA_SECRET_KEY", "").strip()
SUPER_YOOKASSA_SBP_ENABLED = os.getenv("SUPER_YOOKASSA_SBP_ENABLED", "1") == "1"
SUPER_YOOKASSA_AUTO_RENEW_ENABLED = os.getenv("SUPER_YOOKASSA_AUTO_RENEW_ENABLED", "1") == "1"
# Настройки интернет-эквайринга юкасса
PROXY_URL = None
BASE_URL = os.getenv('BASE_URL', 'https://speedycrm.ru')
