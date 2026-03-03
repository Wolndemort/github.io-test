from aiogram.fsm.state import State, StatesGroup


class PaymentStates(StatesGroup):
    waiting_for_receipt = State()


class RegistrationStates(StatesGroup):
    waiting_for_name = State()


class AdminManualAdd(StatesGroup):
    waiting_for_name = State()
    waiting_for_parent_id = State()
    waiting_for_search = State()
    waiting_for_search_visit = State()
    waiting_for_phone = State()


class AdminStates(StatesGroup):
    waiting_for_broadcast_text = State()
