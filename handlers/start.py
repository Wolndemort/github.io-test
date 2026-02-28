from aiogram import Router, types
from aiogram.filters import Command
from handlers.buttons import get_main_menu_keyboard
from database.db import AsyncSessionLocal, User
from aiogram.fsm.context import FSMContext
from loguru import logger

router = Router()

from aiogram import Router, types, F
from aiogram.filters import Command
from handlers.buttons import get_main_menu_keyboard, get_profile_keyboard  # Убедись, что импорты верны
from database.db import AsyncSessionLocal, User, Student  # Добавил Student
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from loguru import logger

router = Router()


@router.message(Command("start"))
async def start_handler(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    full_name = message.from_user.full_name
    first_name = message.from_user.first_name

    logger.info(f"🚀 Команда /start от {full_name} (ID: {user_id})")

    try:
        async with AsyncSessionLocal() as session:
            user = User(user_id=user_id, full_name=full_name)
            await session.merge(user)
            result = await session.execute(select(Student).where(Student.parent_id == user_id))
            students = result.scalars().all()

            await session.commit()
            if students:
                names = ", ".join([s.name for s in students])
                await message.answer(
                    f"<b>Здравствуйте, {first_name}!</b>\n"
                    f"Атлеты в вашем профиле: <b>{names}</b>\n\nЧем могу помочь?",
                    parse_mode="HTML",
                    reply_markup=get_main_menu_keyboard()
                )
            else:
                kb = ReplyKeyboardMarkup(
                    keyboard=[[KeyboardButton(text="🔐 Войти по номеру телефона", request_contact=True)]],
                    resize_keyboard=True,
                    one_time_keyboard=True
                )
                await message.answer(
                    f"<b>Здравствуйте, {first_name}!</b>\n\n"
                    "Похоже, ваш профиль еще не привязан к системе.\n"
                    "Пожалуйста, нажмите кнопку ниже, чтобы войти по номеру телефона:",
                    parse_mode="HTML",
                    reply_markup=kb
                )

    except Exception as e:
        logger.error(f"❌ Ошибка в БД при обработке /start для {user_id}: {e}")
        return await message.answer("Произошла техническая ошибка. Попробуйте позже.")

# Не забудь добавить хендлер для обработки самого контакта (F.contact),
# который мы обсуждали выше, чтобы он «ловил» нажатие этой кнопки.

