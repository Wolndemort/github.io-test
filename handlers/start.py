from aiogram import Router, types
from aiogram.filters import Command
from sqlalchemy.ext.asyncio import AsyncSession
from handlers.buttons import admin_keyboard, get_profile_keyboard
from database.db import User, Student, Club
from sqlalchemy import select
from aiogram.fsm.context import FSMContext

router = Router()


@router.message(Command('start'))
async def start_handler(
    message: types.Message,
    state: FSMContext,
    session: AsyncSession,
    club: Club,
    club_id: int,
    club_settings: dict,     # <--- Настройки для UI
    is_super_admin: bool,
    is_owner: bool
):
    await state.clear()
    user_id = message.from_user.id

    # 1. Регистрация/Обновление пользователя (через merge — это быстрее и чище)
    # Мы привязываем юзера к ID клуба, через который он зашел
    user = await session.merge(User(
        user_id=user_id,
        club_id=club.id,
        full_name=message.from_user.full_name
    ))
    await session.commit()

    # 2. Берем текст приветствия (уже не лезем в БД, всё в памяти)
    welcome_text = club_settings.get("ui", {}).get("welcome_text") or "Добро пожаловать!"

    # 3. Разделение логики (Админ / Клиент)
    if is_owner or is_super_admin:
        return await message.answer(
            f"⚡ **Панель управления клуба «{club.name}»**\n\n{welcome_text}",
            reply_markup=admin_keyboard(club_settings=club_settings, club_id=club_id)
        )

    # 4. Логика клиента (Используем связь relationship из твоих моделей)
    # Поскольку мы сделали merge/get, у объекта user уже могут быть подгружены students
    # Но для надежности и фильтрации по club_id сделаем быстрый select:
    stmt = select(Student).where(
        Student.parent_id == user_id,
        Student.club_id == club.id
    )
    student = (await session.execute(stmt)).scalar_one_or_none()

    if student:
        expire_str = student.expire_date.strftime('%d.%m.%Y') if student.expire_date else "не указано"
        status = f"✅ Атлет: {student.name}\n📅 Абонемент до: {expire_str}"
    else:
        status = "👋 Вы еще не зарегистрированы. Обратитесь к администратору."

    await message.answer(
        f"📍 {club.name}\n\n{welcome_text}\n\n{status}",
        reply_markup=get_profile_keyboard(club_settings=club_settings)
    )
