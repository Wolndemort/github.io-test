from aiogram import Router, types, F
from aiogram.filters import Command
from handlers.buttons import get_main_menu_keyboard
from database.db import Session, User
from config import db_file
from aiogram.fsm.context import FSMContext

router = Router()  # Создаем локальный роутер для этого файла


@router.message(Command("start"))
async def start_handler(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    full_name = message.from_user.full_name
    first_name = message.from_user.first_name

    # Используем SQLAlchemy вместо прямого SQL
    with Session() as session:
        user = session.get(User, user_id)

        if not user:
            # Если юзера нет, создаем его.
            # Поля can_freeze, is_frozen и balance подтянутся из default
            new_user = User(user_id=user_id, full_name=full_name)
            session.add(new_user)
        else:
            # Если юзер уже есть, просто обновляем его имя (аналог ON CONFLICT)
            user.full_name = full_name

        session.commit()

    await message.answer(
        f"<b>Здравствуйте, {first_name}! Какой вопрос?</b>",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard()
    )
