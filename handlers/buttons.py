from datetime import datetime, timezone
from aiogram import Router
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.types import WebAppInfo
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

    # 1. Проход и быстрый доступ
    builder.row(types.InlineKeyboardButton(
        text="📱 Проход по FaceID",
        web_app=WebAppInfo(url=f"{base_url}/webapp/biometric-pass?club_id={user.club_id}&user_id={user.user_id}")
    ))

    builder.row(
        types.InlineKeyboardButton(text="📲 QR-пропуск", callback_data="show_qr"),
        types.InlineKeyboardButton(text="🔍 Мои атлеты", callback_data="detailed_status_info"),
    )
    builder.row(types.InlineKeyboardButton(
        text="🛒 Магазин и корзина (WebApp)",
        web_app=WebAppInfo(url=f"{base_url}/webapp/shop?club_id={user.club_id}")
    ))
    builder.row(
        types.InlineKeyboardButton(text="🧾 История", callback_data="payment_history"),
        types.InlineKeyboardButton(text="💳 Абонемент", callback_data="choose_section"),
    )

    # 2. Дополнительные данные атлетов
    builder.row(types.InlineKeyboardButton(text="✏️ Данные атлетов", callback_data="edit_birthday"))

    # 3. Покупки и абонемент
    if features.get("online_payments", False):
        pass  # Кнопка «💳 Абонемент» выше уже ведёт к покупке.
    if club_settings.get("limits", {}).get("freeze_price_per_day", 0) > 0:
        builder.row(
            types.InlineKeyboardButton(text="❄️ Купить заморозку", callback_data="buy_freeze"),
            types.InlineKeyboardButton(text="💳 Подписка", callback_data="manage_subscription"),
        )

    # 4. Управление атлетами
    builder.row(types.InlineKeyboardButton(text="➕ Добавить атлета", callback_data="add_athlete"))

    # 5. Заморозка действующего абонемента
    if features.get("freeze", True):
        builder.row(types.InlineKeyboardButton(text="❄️ Заморозить абонемент", callback_data="freeze_sub"))

    # 6. Навигация и авторизация
    builder.row(types.InlineKeyboardButton(text="🏠 В главное меню", callback_data="begin"))

    if not is_authorized:
        builder.row(types.InlineKeyboardButton(text="🔐 Привязать профиль по номеру", callback_data="auth_by_phone"))

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
    base_url = f"https://{club_id}.speedycrm.ru"

    # Кнопка сайта (берем из конфига или ставим заглушку)
    site_url = str(ui.get("site_url", "")).strip()
    if ui.get("site_enabled", False) and site_url.startswith(("https://", "http://")):
        builder.button(text="🌐 Наш сайт", url=site_url)

    # Контакты (можно тоже через конфиг, чтобы у каждого клуба свой саппорт)
    if ui.get("support_enabled", True) and str(ui.get("support_link", "")).strip():
        builder.button(text="🆘 Поддержка", url=f"https://t.me/{str(ui['support_link']).strip().lstrip('@')}")

    builder.row(types.InlineKeyboardButton(
        text="🖥 Кабинет клиента",
        web_app=types.WebAppInfo(url=f"{base_url}/webapp/client-cabinet?club_id={club_id}")
    ))
    builder.row(types.InlineKeyboardButton(
        text="🛒 Магазин и корзина",
        web_app=types.WebAppInfo(url=f"{base_url}/webapp/shop?club_id={club_id}")
    ))
    builder.row(types.InlineKeyboardButton(
        text="📅 Расписание клуба",
        web_app=types.WebAppInfo(url=f"{base_url}/webapp/schedule?club_id={club_id}")
    ))

    # 3. ГЛАВНАЯ КНОПКА (Профиль)
    builder.row(types.InlineKeyboardButton(text='👤 Профиль', callback_data='profile'))

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

def admin_keyboard(club_id: int, club_settings: dict, subscription_date: datetime = None, staff_permissions: set[str] | None = None):
    builder = InlineKeyboardBuilder()

    # ⚙️ Достаем фичи из конфига
    features = club_settings.get("features", {})

    # Наивное UTC-время сервера
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)

    # Логика проверки активности CRM
    is_crm_active = True
    if subscription_date:
        if subscription_date.replace(tzinfo=None) <= now_naive:
            is_crm_active = False
    else:
        is_crm_active = False

    # Формируем текст кнопки подписки по чистому datetime напрямую из БД
    if subscription_date:
        days_left = (subscription_date.replace(tzinfo=None) - now_naive).days
        if days_left >= 0:
            sub_text = f"💳 Подписка: {days_left} дн. (Продлить)"
        else:
            sub_text = "❌ Подписка истекла (Оплатить)"
    else:
        sub_text = "❌ Подписка не активна (Оплатить)"

    # --- БЛОК 1: Управление и платежи ---
    allowed = set(staff_permissions or ())
    is_full_access = staff_permissions is None

    first_row = [types.InlineKeyboardButton(text=sub_text, callback_data='pay_menu')]
    if is_full_access or "settings_manage" in allowed:
        first_row.append(types.InlineKeyboardButton(text='🛠 Настройки клуба', callback_data='admin_settings'))
    builder.row(*first_row)

    if is_full_access or "cash_view" in allowed:
        builder.row(types.InlineKeyboardButton(text="💵 Принять наличку", callback_data="admin_cash_list"))

    shop_row = []
    if is_full_access or "cash_sale" in allowed:
        shop_row.append(
            types.InlineKeyboardButton(
                text="🛒 Продать товар",
                web_app=types.WebAppInfo(url=f"https://{club_id}.speedycrm.ru/webapp/admin-product-sale?club_id={club_id}")
            )
        )
    if is_full_access or "products_view" in allowed:
        shop_row.append(
            types.InlineKeyboardButton(
                text="📦 Склад товаров",
                web_app=types.WebAppInfo(url=f"https://{club_id}.speedycrm.ru/webapp/admin-products?club_id={club_id}")
            )
        )
    if shop_row:
        builder.row(*shop_row)

    # --- БЛОК 2: Ежедневная работа ---
    operation_buttons = []
    if (is_full_access or "athletes_manage" in allowed) and features.get("manual_add", True):
        operation_buttons.append(types.InlineKeyboardButton(text="🆕 Добавить атлета", callback_data="admin_add_manual"))

    if (is_full_access or "qr_checkin" in allowed) and features.get("qr_checkin", True):
        operation_buttons.append(types.InlineKeyboardButton(text="📝 Отметить посещение", callback_data="admin_manual_visit"))

    if operation_buttons:
        builder.row(*operation_buttons)

    if is_full_access or "schedule_view" in allowed:
        builder.row(types.InlineKeyboardButton(text="📅 Расписание (Бот)", callback_data="admin_schedule_main"))

    # --- БЛОК 3: Отчёты и коммуникации ---
    report_buttons = []
    if (is_full_access or "reports_view" in allowed) and features.get("daily_report", True):
        report_buttons.append(types.InlineKeyboardButton(text='📊 Дневной отчет', callback_data='daily_report'))

    if (is_full_access or "broadcast" in allowed) and features.get("broadcast", True):
        report_buttons.append(types.InlineKeyboardButton(text='📢 Рассылка', callback_data='admin_broadcast'))

    if report_buttons:
        builder.row(*report_buttons[:2])
        if len(report_buttons) > 2:
            builder.row(*report_buttons[2:])

    # --- БЛОК 4: WebApps (Изоляция через club_id) ---
    base_url = f"https://{club_id}.speedycrm.ru"

    if is_full_access or "table_view" in allowed:
        builder.row(
            types.InlineKeyboardButton(
                text="📊 Таблица (WebApp)",
                web_app=types.WebAppInfo(url=f"{base_url}/admin?club_id={club_id}")
            ),
            types.InlineKeyboardButton(
                text="📹 Камеры (WebApp)",
                web_app=types.WebAppInfo(url=f"{base_url}/webapp/live_cam?club_id={club_id}")
            )
        )
    if is_full_access or "analytics_view" in allowed:
        builder.row(
            types.InlineKeyboardButton(
                text="📈 Вся статистика клуба",
                web_app=types.WebAppInfo(url=f"{base_url}/revenue?club_id={club_id}")
            ),
            types.InlineKeyboardButton(
                text="🗓 Расписание (WebApp)",
                web_app=types.WebAppInfo(url=f"{base_url}/webapp/admin-schedule?club_id={club_id}")
            )
        )
    if is_full_access:
        builder.row(
            types.InlineKeyboardButton(
                text="📜 Аудит",
                web_app=types.WebAppInfo(url=f"{base_url}/webapp/admin-audit?club_id={club_id}")
            )
        )
    if is_full_access or "tariffs_manage" in allowed:
        builder.row(types.InlineKeyboardButton(
            text="💰 Тарифы клуба (WebApp)",
            web_app=types.WebAppInfo(url=f"{base_url}/webapp/admin-tariffs?club_id={club_id}")
        ))
    if is_full_access or "athletes_view" in allowed:
        builder.row(types.InlineKeyboardButton(
            text="👥 Атлеты и ДР",
            web_app=types.WebAppInfo(url=f"{base_url}/admin/students?club_id={club_id}")
        ))
    if is_full_access or "staff_manage" in allowed:
        builder.row(types.InlineKeyboardButton(text="👔 Персонал клуба", callback_data="staff_manage"))
    if is_full_access or "athletes_view" in allowed:
        builder.row(types.InlineKeyboardButton(
            text="📋 Быстрый список атлетов",
            callback_data="admin_quick_athletes"
        ))
    builder.row(types.InlineKeyboardButton(text='🔙 Назад', callback_data='begin'))
    return builder.as_markup()



# --- НАДСТРОЙКА: НАДЁЖНАЯ ФУНКЦИЯ ДЛЯ НИЖНЕЙ КНОПКИ СКУД ---
def get_scanner_keyboard(club_id: int):
    builder = ReplyKeyboardBuilder()

    # Полный путь к сканеру с большими пробелами для удобства
    # Сканер обслуживается самим CRM-доменом, а не GitHub Pages.
    scanner_url = f"https://{club_id}.speedycrm.ru/webapp/scanner?club_id={club_id}&v=106"

    builder.row(types.KeyboardButton(
        text="📸 ОТКРЫТЬ СКАНЕР (ВХОД)",
        web_app=types.WebAppInfo(url=scanner_url)
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



