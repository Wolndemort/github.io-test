import os
from dotenv import load_dotenv

load_dotenv()
db_file = os.getenv('DATABASE_URL', 'sqlite:///database/users.db')
BOT_TOKEN = os.getenv('BOT_TOKEN')
admin_id_str = os.getenv('ADMIN_IDS', '')
ADMIN_IDS = [int(i.strip()) for i in admin_id_str.split(',') if i.strip()]
secret_key = os.getenv('SECRET_KEY')
fastapi_key = os.getenv('API_STATS_KEY')
