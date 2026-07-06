import time
from datetime import datetime
from aiogram import Router
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.types import WebAppInfo, KeyboardButton
from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

router = Router()


def get_profile_keyboard(user, club_settings: dict, is_authorized: bool = False):
    """
    Передаем объект user (модель User из БД), чтобы динамически подставлять
    его club_id и user_id в ссылку WebApp.
    """
    builder = InlineKeyboardBuilder()

    # ⚙️ Достаем фичи из настроек клуба
    features = club_settings.get("features", {})

    # Формируем базовый URL с изоляцией поддомена по club_id
    base_url = f"https://{user.club_id}.speedycrm.ru"

    builder.row(
        types.InlineKeyboardButton(
            text="🍏 Иконка на iPhone",
            url="https://www.icloud.com/shortcuts/124940b85ce8437191757a95b928c59c"  # Твоя проверенная ссылка iCloud
        ),
        types.InlineKeyboardButton(
            text="🤖 Иконка на Android",
            callback_data="show_android_instructions"  # Коллбэк для Android
        )
    )

    # ЕДИНАЯ КНОПКА: Передаем и club_id, и user_id в GET-параметрах
    builder.row(types.InlineKeyboardButton(
        text="📱 Проход по FaceID",
        web_app=WebAppInfo(url=f"{base_url}/webapp/biometric-pass?club_id={user.club_id}&user_id={user.user_id}")
    ))

    # 1. Всегда доступные кнопки
    builder.row(types.InlineKeyboardButton(text='➕ Добавить атлета', callback_data='add_athlete'))
    builder.row(types.InlineKeyboardButton(text='🔍 Подробно об атлетах', callback_data='detailed_status_info'))

    # 2. ДИНАМИЧЕСКИЕ КНОПКИ
    if features.get("online_payments", False):
        builder.row(types.InlineKeyboardButton(text='Купить абонемент 💳', callback_data='choose_section'))

    if features.get("freeze", True):
        builder.row(types.InlineKeyboardButton(text='❄️ Заморозить абонемент', callback_data='freeze_sub'))

    if features.get("qr", True):
        builder.row(types.InlineKeyboardButton(text='📲 МОЙ QR-ПРОПУСК', callback_data='show_qr'))

    # 3. Навигация и Авторизация
    builder.row(types.InlineKeyboardButton(text='НАЧАЛО ◀️🔙', callback_data='begin'))

    if not is_authorized:
        builder.row(types.InlineKeyboardButton(text='🔐 Привязать профиль по номеру', callback_data='auth_by_phone'))

    return builder.as_markup()


def get_main_menu_keyboard(club_settings: dict, club_id: int):
    builder = InlineKeyboardBuilder()

    # 1. ДИНАМИЧЕСКИЕ КНОПКИ (Секции из конфига)
    disciplines = club_settings.get("disciplines", {})

    for code, info in disciplines.items():
        # Выводим только если дисциплина ACTIVE: True
        if info.get("active", True):
            builder.button(
                text=info.get("name", code.upper()),
                callback_data=f"section_{code}"  # Ведет на меню секции (цены/расписание)
            )

    # 2. СТАТИЧЕСКИЕ / НАСТРОЙКИ UI
    ui = club_settings.get("ui", {})

    # Кнопка сайта (берем из конфига или ставим заглушку)
    site_url = ui.get("site_url", "https://aemaykop.ru")
    builder.button(text="🌐 Наш сайт", url=site_url)

    # Контакты (можно тоже через конфиг, чтобы у каждого клуба свой саппорт)
    builder.button(text="📞 Контакты", callback_data="contact")

    # 3. ГЛАВНАЯ КНОПКА (Профиль)
    builder.row(types.InlineKeyboardButton(text='👤 МОЙ ПРОФИЛЬ', callback_data='profile'))

    builder.adjust(2)  # Кнопки по 2 в ряд, профиль на всю ширину (row)
    return builder.as_markup()


def get_section_menu_kb(discipline_code: str, discipline_name: str):
    builder = InlineKeyboardBuilder()

    # 1. Кнопка цен (код секции подставится сам: price_bjj, price_boxing)
    builder.row(types.InlineKeyboardButton(
        text=f'💰 Цены: {discipline_name}',
        callback_data=f'price_{discipline_code}')
    )

    # 2. Кнопка расписания
    builder.row(types.InlineKeyboardButton(
        text=f'📅 Расписание: {discipline_name}',
        callback_data=f'schedule_{discipline_code}')
    )

    # 3. Кнопка покупки
    builder.row(types.InlineKeyboardButton(
        text='💳 Купить абонемент',
        callback_data=f'buy_{discipline_code}')
    )

    builder.row(types.InlineKeyboardButton(text='⬅️ В главное меню', callback_data='begin'))

    return builder.as_markup()


def admin_keyboard(club_settings: dict, club_id: int, subscription_date: datetime = None):
    builder = InlineKeyboardBuilder()

    # ⚙️ Достаем фичи из конфига
    features = club_settings.get("features", {})

    # --- БЛОК 1: Управление (Всегда доступны владельцу) ---
    builder.row(types.InlineKeyboardButton(text='🛠 Настройки клуба', callback_data='admin_settings'))
    builder.row(types.InlineKeyboardButton(text="💵 Принять наличку", callback_data="admin_cash_list"))

    sub_text = "💳 Продлить подписку"
    if subscription_date:
        days_left = (subscription_date - datetime.now()).days
        if days_left >= 0:
            sub_text = f"💳 Подписка: {days_left} дн. (Продлить)"
        else:
            sub_text = "❌ Подписка истекла (Оплатить)"

    # --- БЛОК 0: Финансы SaaS (Новый блок) ---
    builder.row(types.InlineKeyboardButton(text=sub_text, callback_data='pay_menu'))

    # --- БЛОК 2: Динамические фичи (Проверка по конфигу) ---
    if features.get("daily_report", True):
        builder.row(types.InlineKeyboardButton(text='📊 Дневной отчет', callback_data='daily_report'))

    if features.get("broadcast", True):
        builder.row(types.InlineKeyboardButton(text='📢 Рассылка', callback_data='admin_broadcast'))

    if features.get("manual_add", True):
        builder.row(types.InlineKeyboardButton(text="🆕 Добавить (вручную)", callback_data="admin_add_manual"))

    if features.get("qr_checkin", True):  # Посещения обычно связаны с QR или ручной отметкой
        builder.row(types.InlineKeyboardButton(text="📝 Отметить посещение", callback_data="admin_manual_visit"))
    
    builder.row(types.InlineKeyboardButton(text="📅 Расписание (Бот)", callback_data="admin_schedule_main"))
    
    if features.get("export", True):
        builder.row(types.InlineKeyboardButton(text='📥 Выгрузка БД (CSV)', callback_data='export_db'))

    # --- БЛОК 3: WebApps (Изоляция через club_id) ---
    base_url = f"https://{club_id}.speedycrm.ru"
    nocache_url = f"{base_url}/webapp/cameras?club_id={club_id}&v={int(time.time())}"

    builder.row(types.InlineKeyboardButton(
        text="📹 Камеры (WebApp)",
        web_app=types.WebAppInfo(url=nocache_url)
    ))

    builder.row(types.InlineKeyboardButton(
        text="📊 Таблица (WebApp)",
        web_app=WebAppInfo(url=f"{base_url}/admin?club_id={club_id}"))
    )
    
    # ИСПРАВЛЕНО: Убраны лишние пробелы в начале строки и добавлены )) в конце
    builder.row(types.InlineKeyboardButton(
        text="🗓 Расписание (WebApp)",
        web_app=WebAppInfo(url=f"{base_url}/webapp/schedule?club_id={club_id}")
    ))
    
    builder.row(types.InlineKeyboardButton(
        text="📈 Отчет Pandas",
        web_app=WebAppInfo(url=f"{base_url}/revenue?club_id={club_id}"))
    )
    builder.row(types.InlineKeyboardButton(
        text="📄 Выгрузка в Excel",
        web_app=WebAppInfo(url=f"{base_url}/stats/export/excel?club_id={club_id}"))
    )

    builder.row(types.InlineKeyboardButton(text='🔙 Назад', callback_data='begin'))

    return builder.as_markup()



def get_scanner_keyboard(club_settings: dict, club_id: int):
    # 1. Проверяем, включен ли модуль QR в настройках этого клуба
    features = club_settings.get("features", {})

    if not features.get("qr_checkin", True):
        # Если выключено — возвращаем пустую клавиатуру (или удаляем ее)
        return types.ReplyKeyboardRemove()

    builder = ReplyKeyboardBuilder()

    # 2. Формируем URL с параметром club_id (чтобы сканер знал, чей это атлет)
    # Если твой сканер на github.io умеет принимать параметры:
    scanner_url = f"https://wolndemort.github.io/github.io-test/scanner.html{club_id}"

    builder.row(KeyboardButton(
        text="📸 ОТКРЫТЬ СКАНЕР (ВХОД)",
        web_app=WebAppInfo(url=scanner_url)
    ))

    return builder.as_markup(resize_keyboard=True)


def discipline(club_settings: dict):
    builder = InlineKeyboardBuilder()
    disciplines = club_settings.get("disciplines", {})

    # Рисуем т4607016282459
    # олько те кнопки, которые АКТИВНЫ в этом клубе
    for code, info in disciplines.items():
        if info.get("active"):
            builder.row(types.InlineKeyboardButton(
                text=f"🥋 {info['name']}",
                callback_data=f"buy_{code}"
            ))

    # Статичные кнопки управления
    builder.row(types.InlineKeyboardButton(text='👤 МОЙ ПРОФИЛЬ', callback_data='profile'))
    builder.row(types.InlineKeyboardButton(text='⬅️ В главное меню', callback_data='begin'))

    return builder.as_markup()


def get_pay_options_kb(discipline_cfg: dict, sport_type: str) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    tariffs = discipline_cfg.get("tariffs", [])
    d_type = discipline_cfg.get("type", "lessons")

    for idx, tariff in enumerate(tariffs):
        price = tariff.get("price")
        days = tariff.get("days")
        count = tariff.get("count")

        if d_type == "unlimited" or count == 999:
            btn_text = f"♾ Безлимит {days} дн. — {price}₽"
        else:
            btn_text = f"🔢 {count} зан. / {days} дн. — {price}₽"

        # ВАЖНО: Передаем ИНДЕКС тарифа: set_tariff_[sport_type]_[idx]
        builder.row(types.InlineKeyboardButton(
            text=btn_text,
            callback_data=f"set_tariff_{sport_type}_{idx}"
        ))

    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="choose_section"))
    return builder.as_markup()


def get_cash_options_kb(discipline_cfg: dict) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    tariffs = discipline_cfg.get("tariffs", [])
    d_type = discipline_cfg.get("type", "lessons")

    for idx, t in enumerate(tariffs):
        price = t.get("price")
        days = t.get("days")
        count = t.get("count")

        # 1. Формируем понятный для админа текст кнопки
        if d_type == "unlimited" or count == 999:
            # Для безлимита прячем число 999 и пишем красивый текст
            btn_text = f"♾ Безлимит {days} дн. — {price}₽"
        else:
            # Для обычных занятий выводим лимит и срок
            btn_text = f"🔢 {count} зан. / {days} дн. — {price}₽"

        # 2. В callback_data передаем ИНДЕКС тарифа в списке (0, 1, 2...)
        builder.row(types.InlineKeyboardButton(
            text=btn_text,
            callback_data=f"confirm_cash_{idx}"
        ))

    # Кнопка возврата в список (используем ваш callback_data)
    builder.row(types.InlineKeyboardButton(text="🔙 Назад", callback_data="admin_cash_list"))
    return builder.as_markup()

