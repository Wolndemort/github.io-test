from aiogram import Router, types, F
from aiogram.filters import Command
from handlers.buttons import get_main_menu_keyboard
from database.db import Session, User

from aiogram.fsm.context import FSMContext
from loguru import logger

router = Router()  # Создаем локальный роутер для этого файла


@router.message(Command("start"))
async def start_handler(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    full_name = message.from_user.full_name
    first_name = message.from_user.first_name
    logger.info(f"🚀 Команда /start от {full_name} (ID: {user_id})")


    try:
        with Session() as session:
            user = session.get(User, user_id)
            if not user:
                logger.success(f"🆕 Новый пользователь зарегистрирован: {full_name} (ID: {user_id})")
                new_user = User(user_id=user_id, full_name=full_name)
                session.add(new_user)
            else:
                logger.debug(f"🔄 Старый пользователь {user_id} зашел, обновляем full_name")
                user.full_name = full_name
            session.commit()
    except Exception as e:
        logger.error(f"❌ Ошибка в БД при обработке /start для {user_id}: {e}")
        return await message.answer("Произошла техническая ошибка. Попробуйте позже.")

    await message.answer(
        f"<b>Здравствуйте, {first_name}! Какой вопрос?</b>",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard()
    )
