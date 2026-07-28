from aiogram.fsm.state import State, StatesGroup


class PaymentStates(StatesGroup):
    waiting_for_receipt = State()
    waiting_for_freeze_days = State()


class RegistrationStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_birthday = State()
    waiting_for_birthday_edit = State()


class AdminManualAdd(StatesGroup):
    waiting_for_name = State()
    waiting_for_discipline = State()
    waiting_for_tariff = State()
    waiting_for_search = State()
    waiting_for_search_visit = State()
    waiting_for_phone = State()
    waiting_for_birthday = State()


class AdminStates(StatesGroup):
    waiting_for_broadcast_text = State()
    waiting_for_staff_telegram_id = State()
    waiting_for_staff_role = State()
    waiting_for_parent_id = State()

# --- ДОБАВЛЯЕМ ДЛЯ SAAS СИСТЕМЫ ---


class AddClub(StatesGroup):
    waiting_for_name = State()
    waiting_for_owner_id = State()  # <--- Добавили, чтобы знать кому бот принадлежит
    waiting_for_token = State()


class SuperAdminStates(StatesGroup):
    waiting_for_staff_telegram_id = State()
    waiting_for_staff_role = State()
    waiting_for_broadcast_text = State()
    waiting_for_extend_days = State()    # Если захочешь вводить дни продления вручную
    waiting_for_club_search = State()    # Поиск клуба по ID или названию


class AdminSettings(StatesGroup):
    waiting_for_payment_info = State()


class TurnstileSetup(StatesGroup):
    wait_for_url = State()
    wait_for_password = State()


class AdminTariffStates(StatesGroup):
    # Состояния для быстрого редактирования полей текущего тарифа
    waiting_for_price = State()
    waiting_for_days = State()
    waiting_for_count = State()
    waiting_for_min_age = State()  # Для изменения возраста в готовом тарифе
    add_min_age = State()
    # Состояния для пошагового мастера создания нового тарифа
    add_price = State()
    add_days = State()
    add_count = State()
    
    
class AdminScheduleStates(StatesGroup):
    choose_day = State()         # Выбор дня недели кнопками
    add_time = State()           # Ожидание ввода времени (напр. 19:00)
    add_coach = State()          # Ожидание ввода тренера/группы
    add_slots = State()          # Ожидание ввода лимита мест (макс. мест)


class YooKassaSetupStates(StatesGroup):
    waiting_for_shop_id = State()      # Ожидание ввода Shop ID
    waiting_for_secret_key = State()   # Ожидание ввода Секретного ключа




class AdminSettingsSG(StatesGroup):
    waiting_for_public_links = State()
    waiting_for_site_url = State()
    waiting_for_support_username = State()
    # Твои старые состояния (например, для ввода ключей ЮKassa)
    waiting_for_session_timeout = State()  # Ждем ввод минут сессии
    waiting_for_freeze_step = State()      # Ждем ввод дней заморозки
    waiting_for_freeze_price = State()     # Ждем цену 1 дня платной заморозки
    waiting_for_loading_config = State()
    waiting_for_loading_logo = State()
