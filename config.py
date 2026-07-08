import os
from dotenv import load_dotenv

load_dotenv()
db_file = os.getenv('DATABASE_URL', 'sqlite:///database/users.db')
BOT_TOKEN = os.getenv('BOT_TOKEN')
admin_id_str = os.getenv('ADMIN_IDS', '')
ADMIN_IDS = [int(i.strip()) for i in admin_id_str.split(',') if i.strip()]
secret_key = os.getenv('SECRET_KEY')
fastapi_key = os.getenv('API_STATS_KEY')
# Настройки интернет-эквайринга Т-Банка
T_BANK_TERMINAL_KEY = os.getenv('T_BANK_TERMINAL_KEY', '')
T_BANK_SECRET_KEY = os.getenv('T_BANK_SECRET_KEY', '')
T_BANK_NOTIFICATION_URL = os.getenv('T_BANK_NOTIFICATION_URL', '')
