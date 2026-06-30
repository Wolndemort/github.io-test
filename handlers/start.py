from aiogram import Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import ReplyKeyboardBuilder  # Чистый импорт для текстовой кнопки
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from handlers.buttons import admin_keyboard, get_profile_keyboard
from database.db import User, Student, Club

router = Router()


@router.message(Command('start'))
async def start_handler(
        message: types.Message,
        state: FSMContext,
        session: AsyncSession,
        club: Club,
        club_id: int,
        club_settings: dict,  # Настройки для UI из памяти
        is_super_admin: bool,
        is_owner: bool
):
    await state.clear()
    user_id = message.from_user.id

    # 1. Регистрация/Обновление пользователя (убрали неиспользуемую переменную user)
    await session.merge(User(
        user_id=user_id,
        club_id=club.id,
        full_name=message.from_user.full_name
    ))
    await session.commit()

    # 2. Берем текст приветствия из памяти
    welcome_text = club_settings.get("ui", {}).get("welcome_text") or "Добро пожаловать!"

    # 3. Разделение логики (Админ / Владелец / Суперадмин)
    if is_owner or is_super_admin:
        return await message.answer(
            f"⚡ <b>Панель управления клуба «{club.name}»</b>\n\n{welcome_text}",
            reply_markup=admin_keyboard(club_settings=club_settings, club_id=club_id),
            parse_mode="HTML"
        )

    # 4. Логика клиента (Ищем привязанного атлета по Telegram ID)
    stmt = select(Student).where(
        Student.parent_id == user_id,
        Student.club_id == club.id
    )
    student = (await session.execute(stmt)).scalar_one_or_none()

    if student:
        expire_str = student.expire_date.strftime('%d.%m.%Y') if student.expire_date else "не указано"
        status_text = f"✅ Атлет: <b>{student.name}</b>\n📅 Абонемент до: <b>{expire_str}</b>"

        await message.answer(
            f"📍 <b>{club.name}</b>\n\n{welcome_text}\n\n{status_text}",
            reply_markup=get_profile_keyboard(club_settings=club_settings, is_authorized=True),
            parse_mode="HTML"
        )
    else:
        # Используем импортированный напрямую ReplyKeyboardBuilder без префикса types.
        builder = ReplyKeyboardBuilder()
        builder.row(types.KeyboardButton(
            text="📱 Войти по номеру телефона",
            request_contact=True
        ))

        status_text = (
            "👋 Рады видеть вас!\n\n"
            "Если администратор клуба уже внёс вас в базу данных, нажмите кнопку ниже, "
            "чтобы подтвердить свой номер телефона и войти в личный кабинет."
        )

        await message.answer(
            f"📍 <b>{club.name}</b>\n\n{welcome_text}\n\n{status_text}",
            reply_markup=builder.as_markup(resize_keyboard=True, one_time_keyboard=True),
            parse_mode="HTML"
        )
