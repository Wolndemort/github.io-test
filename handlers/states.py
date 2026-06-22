from aiogram.fsm.state import State, StatesGroup


class PaymentStates(StatesGroup):
    waiting_for_receipt = State()


class RegistrationStates(StatesGroup):
    waiting_for_name = State()


class AdminManualAdd(StatesGroup):
    waiting_for_name = State()
    waiting_for_lessons = State()
    waiting_for_parent_id = State()
    waiting_for_search = State()
    waiting_for_search_visit = State()
    waiting_for_phone = State()


class AdminStates(StatesGroup):
    waiting_for_broadcast_text = State()

# --- ДОБАВЛЯЕМ ДЛЯ SAAS СИСТЕМЫ ---


class AddClub(StatesGroup):
    waiting_for_name = State()
    waiting_for_owner_id = State()  # <--- Добавили, чтобы знать кому бот принадлежит
    waiting_for_token = State()


class SuperAdminStates(StatesGroup):
    waiting_for_broadcast_text = State()
    waiting_for_extend_days = State()    # Если захочешь вводить дни продления вручную
    waiting_for_club_search = State()    # Поиск клуба по ID или названию


class AdminSettings(StatesGroup):
    waiting_for_payment_info = State()


class TurnstileSetup(StatesGroup):
    wait_fot_url = State()
    wait_for_password = State()
