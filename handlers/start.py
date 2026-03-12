from aiogram import Router, types
from aiogram.filters import Command
from sqlalchemy.ext.asyncio import AsyncSession
from handlers.buttons import get_main_menu_keyboard,get_profile_keyboard
from database.db import User, Student
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from loguru import logger

router = Router()


@router.message(Command("start"))
async def start_handler(message: types.Message, state: FSMContext, session: AsyncSession):
    await state.clear()
    user_id = message.from_user.id
    full_name = message.from_user.full_name
    first_name = message.from_user.first_name
    logger.info(f"🚀 Команда /start от {full_name} (ID: {user_id})")

    try:
        # --- БЛОК РАБОТЫ С ПОЛЬЗОВАТЕЛЕМ ---
        # 1. Пытаемся получить юзера из базы
        db_user = await session.get(User, user_id)

        if not db_user:
            # Если нет — создаем нового
            db_user = User(user_id=user_id, full_name=full_name)
            session.add(db_user)
        else:
            # Если есть — просто обновляем имя (на случай, если сменил в ТГ)
            db_user.full_name = full_name

        # 2. Ищем привязанных студентов
        stmt = select(Student).where(Student.parent_id == user_id)
        result = await session.execute(stmt)
        students = result.scalars().all()

        # Коммитим один раз в конце блока работы с данными
        await session.commit()

        # --- БЛОК ОТВЕТА ---
        if students:
            names = ", ".join([s.name for s in students])
            await message.answer(
                f"<b>Здравствуйте, {first_name}!</b>\n"
                f"Атлеты в вашем профиле: <b>{names}</b>\n\n"
                "Чем могу помочь?",
                parse_mode="HTML",
                reply_markup=get_main_menu_keyboard()
            )
        else:
            # Передаем False, чтобы кнопка авторизации ТОЧНО появилась
            kb = get_profile_keyboard(is_authorized=False)
            await message.answer(
                f"<b>Здравствуйте, {first_name}!</b>\n\n"
                "Похоже, ваш профиль еще не привязан к системе.\n"
                "Пожалуйста, нажмите кнопку ниже, чтобы авторизоваться:",
                parse_mode="HTML",
                reply_markup=kb
            )

    except Exception as e:
        await session.rollback()
        logger.error(f"❌ Ошибка в БД при обработке /start для {user_id}: {e}")
        return await message.answer("Произошла техническая ошибка. Попробуйте позже.")




