import sqlite3
from config import db_file
from datetime import datetime, timedelta


def init_db():
    conn = sqlite3.connect(db_file, check_same_thread=False)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS users
                    (user_id INTEGER PRIMARY KEY,
                     full_name TEXT,
                     expire_date TEXT)''')
    try:
        cur.execute("ALTER TABLE users ADD COLUMN full_name TEXT")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    cur.close()
    conn.close()
    print("база готова к работе")


def get_expire_users():
    today = datetime.now().strftime('%Y-%m-%d')
    three_days_limit = (datetime.now()+timedelta(days=3)).strftime('%Y-%m-%d')

    with sqlite3.connect(db_file) as conn:
        cur = conn.cursor()
        query = """
            SELECT user_id, full_name FROM users
            WHERE substr( expire_date, 1, 10) <=?
             AND substr(expire_date, 1, 10) >= ?
        """
    cur.execute(query, (three_days_limit, today))
    return cur.fetchall()


def has_subscription(user_id):
    with sqlite3.connect(db_file) as conn:
        cur = conn.cursor()
        cur.execute("SELECT expire_date FROM users WHERE user_id = ?", (user_id,))
        result = cur.fetchone()

    if result:
        expire_date = datetime.strptime(result[0], '%Y-%m-%d %H:%M:%S')
        if expire_date > datetime.now():
            return True, expire_date  # Активен
        else:
            return False, expire_date  # Просрочен
    return None, None  # Юзера вообще нет в базе


def add_abon(user_id):
    with sqlite3.connect(db_file) as conn:
        cur = conn.cursor()
        cur.execute('SELECT expire_date from users WHERE user_id = ?', (user_id,))
        result = cur.fetchone()

        now = datetime.now()

        if result and result[0]:
            try:
                current_expire = datetime.strptime(result[0], '%Y-%m-%d %H:%M:%S')
                start_date = max(now, current_expire)
            except ValueError:
                start_date = now
        else:
            start_date = now

        expire_new_dt = start_date + timedelta(days=30)
        expire_new_str = expire_new_dt.strftime('%Y-%m-%d %H:%M:%S')

        cur.execute("""
            INSERT INTO users (user_id, expire_date)
            VALUES(?, ?)
            ON CONFLICT(user_id) DO UPDATE SET expire_date = excluded.expire_date
        """, (user_id, expire_new_str))
        conn.commit()

    return expire_new_str


def get_all_users_count():
    with sqlite3.connect(db_file) as conn:
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM users')
        return cur.fetchone()


def get_active_subs_count():
    with sqlite3.connect(db_file) as conn:
        cur = conn.cursor()
        today = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cur.execute('SELECT COUNT(*) FROM users WHERE expire_date > ?', (today,))
        return cur.fetchone()[0]
