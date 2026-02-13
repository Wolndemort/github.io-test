import time
import logging
import sqlite3
import os
import telebot
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telebot import types
from telebot.types import LabeledPrice
from apscheduler.schedulers.background import BackgroundScheduler


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='bot_log.txt',
    filemode='a'

)


load_dotenv()
logging.info('бот запущен и лог работает')
bot = telebot.TeleBot(os.getenv('BOT_TOKEN'))
db_file = os.getenv('DB_NAME')
bnt14 = types.InlineKeyboardButton('НАЧАЛО ◀️🔙', callback_data='begin')
but13 = types.InlineKeyboardButton('Купить аббонемент', callback_data='buy')
btn100 = types.InlineKeyboardButton('Проверить статус абонемента', callback_data='profile')


def init_db():
    conn = sqlite3.connect(db_file, check_same_thread=False)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS users
                    (user_id INTEGER PRIMARY KEY, expire_date TEXT)''')
    conn.commit()
    cur.close()
    conn.close()


def check_abon():
    global but13
    markup = types.InlineKeyboardMarkup()
    markup.add(but13)
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')
    three_days_limit = (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d')
    query = """
       SELECT user_id FROM users
       WHERE substr(expire_date, 1, 10) <= ?
       AND substr(expire_date, 1, 10) >= ?
       """
    try:
        cur.execute(query, (three_days_limit, today))
        users = cur.fetchall()
        for user in users:
            try:
                bot.send_message(user[0], '⚠️ Внимание! Ваш абонемент скоро истекает . Не забудьте продлить его! 🥊',
                                 reply_markup=markup)
            except Exception as err:
                print(f"Не удалось отправить сообщение {user[0]}: {err}")
    finally:
        conn.close()


# Альтернативный (более красивый) способ
# Вместо обращения по индексу, можно «распаковать» кортеж прямо в цикле. Это сделает код понятнее:
# python
# for (user_id,) in users: # Запятая важна — она распаковывает кортеж из одного элемента
#     try:
#         bot.send_message(user_id, '⚠️ Текст...')
#     except Exception as e:
#         print(f"Ошибка для {user_id}: {e}")
# Используйте код с осторожностью.
#
# В этом случае вам не придется писать [0], так как переменная user_id сразу будет содержать число.
# альтернатива написания


scheduler = BackgroundScheduler()
# scheduler.add_job(check_abon, 'interval', seconds=10)тестовая функция за 3 дня до окончания .
scheduler.add_job(check_abon, 'cron', hour=12, minute=42)


@bot.message_handler(commands=['start'])
def start(message):
    global btn100
    user_first_name = message.from_user.first_name
    user_last_name = message.from_user.last_name or '.'
    markup = types.InlineKeyboardMarkup()
    btn1 = types.InlineKeyboardButton('MMA', callback_data='mma')
    byn2 = types.InlineKeyboardButton('BJJ', callback_data='bjj')
    bnn3 = types.InlineKeyboardButton('GRAPPLING KIDS', callback_data='kids')
    web_app = types.WebAppInfo(url='https://wolndemort.github.io/github.io-test/')
    btn4 = types.InlineKeyboardButton("Абонементы", web_app=web_app)
    btn5 = types.InlineKeyboardButton("Контакты", callback_data='contact')
    bnt6 = types.InlineKeyboardButton('Открыть сайт', url="https://aemaykop.ru/")
    markup.add(btn1, byn2, bnn3, btn4, btn5, bnt6, btn100, row_width=2)
    bot.send_message(message.chat.id, f"<b>Здравствуйте,{user_first_name} {user_last_name} какой у вас Вопрос❓</b>",
                     parse_mode='HTML', reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data == 'begin')
def begin(call):
    call.message.from_user = call.from_user
    start(call.message)


@bot.callback_query_handler(func=lambda call: call.data == 'mma')
def next_step(call):
    global bnt14
    markup1 = types.InlineKeyboardMarkup()
    bnt7 = types.InlineKeyboardButton('Стоимость абонементов👊💸', callback_data='price mma')
    bnt8 = types.InlineKeyboardButton('Расписание занятий👊🗓', callback_data='schedule mma')
    markup1.add(bnt7, bnt8, bnt14, row_width=1)
    bot.send_message(call.message.chat.id, 'Информация по MMA👊', reply_markup=markup1)


@bot.callback_query_handler(func=lambda call: call.data == 'bjj')
def next_step1(call):
    global bnt14
    markup2 = types.InlineKeyboardMarkup()
    bnt9 = types.InlineKeyboardButton('Стоимость абонементов💸🥋', callback_data='price bjj')
    bnt10 = types.InlineKeyboardButton('Расписание занятий🗓🥋', callback_data='schedule bjj')
    markup2.add(bnt9, bnt10, bnt14, row_width=1)
    bot.send_message(call.message.chat.id, 'Информация по Джиу-джитсу🥋', reply_markup=markup2)


@bot.callback_query_handler(func=lambda call: call.data == 'kids')
def nex_step2(call):
    markup3 = types.InlineKeyboardMarkup()
    bnt9 = types.InlineKeyboardButton('Стоимость абонементов💸👶', callback_data='price kids')
    bnt10 = types.InlineKeyboardButton('Расписание занятий🗓👶', callback_data='schedule kids')
    markup3.add(bnt9, bnt10, bnt14, row_width=1)
    bot.send_message(call.message.chat.id, 'Информация по детским абонементам👶 ', reply_markup=markup3)


@bot.callback_query_handler(func=lambda call: call.data == 'price mma')
def func1(call):
    global bnt14, but13
    photo = '1111.jpg'
    with open(photo, 'rb') as img:
        but = types.InlineKeyboardMarkup()
        but.add(but13, bnt14)
        bot.send_photo(call.message.chat.id, img,
                       caption="Абонемент на месяц\n"
                               "Цена - 5000 тыясч рублей\n"
                               "Срок действия -"
                               " 30 рабочих дней с момента покупки\n"
                               "Заморозка - 5 дней\n"
                               "В абонемент входят все занятия проводимые в месяц согласно расписанию",
                       reply_markup=but)


@bot.callback_query_handler(func=lambda call: call.data == 'price bjj')
def func1(call):
    global bnt14, but13
    photo = '1111.jpg'
    with open(photo, 'rb') as img:
        but = types.InlineKeyboardMarkup()
        but.add(but13, bnt14)
        bot.send_photo(call.message.chat.id, img,
                       caption="Абонемент на месяц\n"
                               "Цена - 5000 тыясч рублей\n"
                               "Срок действия - 30 рабочих дней с момента покупки\n"
                               "Заморозка - 5 дней\n"
                               "В абонемент входят все занятия проводимые в месяц согласно расписанию",
                       reply_markup=but)


@bot.callback_query_handler(func=lambda call: call.data == 'price kids')
def func1(call):
    global bnt14, but13
    photo = '1111.jpg'
    with open(photo, 'rb') as img:
        but = types.InlineKeyboardMarkup()
        but.add(but13, bnt14)
        bot.send_photo(call.message.chat.id, img,
                       caption="Абонемент на месяц\n"
                               "Цена - 5000 тыясч рублей\n"
                               "Срок действия - 30 рабочих дней с момента покупки\n"
                               "Заморозка - 5 дней\n"
                               "В абонемент входят все занятия проводимые в месяц согласно расписанию",
                       reply_markup=but)


@bot.callback_query_handler(func=lambda call: call.data == 'buy')
def buy_mma(call):
    prices = [LabeledPrice('Аббонемент на 30 дней', amount=1)]
    bot.send_invoice(
        call.message.chat.id,
        title="Абонемент на месяц.",
        description='Доступ ко всем тренеровкам на 30 дней согласно расписанию',
        invoice_payload="month abon",
        provider_token="",
        currency="XTR",
        prices=prices
    )


@bot.pre_checkout_query_handler(func=lambda query: True)
def checkout(pre_checkout_query):
    bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)
    bot.send_message(pre_checkout_query.from_user.id, "Проверяем ваш платеж...")


@bot.message_handler(content_types=['successful_payment'])
def got_payment(message):
    user_id = message.from_user.id
    payment_info = message.successful_payment
    add_abon(user_id)
    admin_id = 1271717628
    admin_text = (
        f"🔔 **Новая оплата!**\n\n"
        f"👤 Клиент: {message.from_user.full_name}\n"
        f"🆔 ID пользователя: `{user_id}`\n"
        f"🏷 Товар: {payment_info.invoice_payload}\n"
        f"💰 Сумма: {payment_info.total_amount} Stars (XTR)"
    )
    try:
        bot.send_message(admin_id, admin_text, parse_mode="Markdown")
    except Exception as error:
        print(f"Ошибка при отправке уведомления админу: {error}")
    global btn100
    markup100 = types.InlineKeyboardMarkup()
    markup100.add(btn100)
    bot.send_message(message.chat.id, f'Оплата прошла успешно!', reply_markup=markup100)


def add_abon(user_id):
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    expire_new = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
    cur.execute("INSERT OR REPLACE INTO users (user_id, expire_date) VALUES(?, ?)"
                "ON CONFLICT(user_id) DO UPDATE SET expire_date = excluded.expire_date", (user_id, expire_new))
    conn.commit()
    conn.close()


def has_subscription(user_id):
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute("SELECT expire_date FROM users WHERE user_id = ?", (user_id,))
    result = cur.fetchone()
    conn.close()
    if result:
        expire_date = datetime.strptime(result[0], '%Y-%m-%d %H:%M:%S')
        if expire_date > datetime.now():
            return True, expire_date
        else:
            return False, expire_date


@bot.callback_query_handler(func=lambda call: call.data == 'profile')
def show_profile(call):
    user_id = call.from_user.id
    is_active, expire_date = has_subscription(user_id)
    if is_active:
        date_str = expire_date.strftime('%d.%m.%Y %H:%M')
        bot.send_message(user_id,
                         f"✅ Ваш абонемент активен!\n📅 Истекает: {date_str}\n Вам прийдет уведомление об окончании!")
        call.message.from_user = call.from_user
        start(call.message)
    elif expire_date:
        bot.send_message(user_id, "❌ Ваш абонемент истек. Продлите его кнопкой /buy")
    else:
        bot.send_message(user_id, "💎 У вас нет активного абонемента. Купите доступ через /buy")


if __name__ == '__main__':
    init_db()
    scheduler.start()

    while True:
        try:
            bot.polling(none_stop=True)
        except Exception as e:
            print(f'ошибка: {e}')
            time.sleep(5)


# добавил енв и шгноргитхуб , так же логирование , нужно добавить в обработчики
# ошибок логирование есть скрин в телефоне , дальше подключить анализатор к примеру пандас с майсикьюэль,
# так же прочитать про гит адд и гит инит

#сейчас бот регестрирует людей с момента покупки абона и не может проверить статус абонеиента
# нужно поменять что бы человек мог отследить статус даже если у него нет абонемента , так же рудольфу вместо фамии поставилось none
#докерс
#агрегации джоин объединение таблиц и самое основное гитхуб ветки .