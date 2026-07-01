from aiogram import Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

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

    # 1. Регистрация/Обновление пользователя
    db_user = await session.get(User, user_id)

    if not db_user:
        # Если юзера нет, создаем новую запись с is_accepted=False
        db_user = User(
            user_id=user_id,
            club_id=club.id,
            full_name=message.from_user.full_name,
            is_accepted=False
        )
        session.add(db_user)
        await session.commit()
    else:
        # Если юзер есть, но сменил имя в Телеге — обновляем его
        if db_user.full_name != message.from_user.full_name:
            db_user.full_name = message.from_user.full_name
            await session.commit()

    # 2. ПРОВЕРКА ЮРИДИЧЕСКОГО СОГЛАСИЯ
    # Блокируем меню, если юзер еще не нажал кнопку «Принять»
    if not db_user.is_accepted:
        # Формируем боевой URL на основе твоего поддомена speedycrm.ru
        base_url = f"https://{club_id}.speedycrm.ru"

        inline_builder = InlineKeyboardBuilder()
        inline_builder.row(types.InlineKeyboardButton(
            text="📄 Политика конфиденциальности",
            web_app=types.WebAppInfo(url=f"{base_url}/privacy")
        ))
        inline_builder.row(types.InlineKeyboardButton(
            text="📜 Публичная оферта",
            web_app=types.WebAppInfo(url=f"{base_url}/oferta")
        ))
        inline_builder.row(types.InlineKeyboardButton(
            text="✅ Принять и продолжить",
            callback_data="accept_legal_rules"
        ))

        legal_text = (
            f"📍 <b>{club.name}</b>\n\n"
            "Рады видеть вас! Чтобы получить доступ к личному кабинету и системе СКУД (турникету), "
            "вам необходимо ознакомиться и согласиться с условиями обработки персональных данных и публичной офертой.\n\n"
            "Вы можете прочитать документы прямо внутри Telegram, нажав на кнопки ниже. "
            "После прочтения нажмите кнопку <b>«Принять и продолжить»</b>:"
        )

        return await message.answer(
            legal_text,
            reply_markup=inline_builder.as_markup(),
            parse_mode="HTML"
        )

    # --- ОСТАЛЬНАЯ ЛОГИКА БЕЗ ИЗМЕНЕНИЙ ---

    # 3. Настройки UI из памяти
    welcome_text = club_settings.get("ui", {}).get("welcome_text") or "Добро пожаловать!"

    # 4. Разделение логики (Админ / Владелец / Суперадмин)
    if is_owner or is_super_admin:
        return await message.answer(
            f"⚡ <b>Панель управления клуба «{club.name}»</b>\n\n{welcome_text}",
            reply_markup=admin_keyboard(club_settings=club_settings, club_id=club_id),
            parse_mode="HTML"
        )

    # 5. Логика клиента (Ищем привязанного атлета по Telegram ID)
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


# ОБРАБОТЧИК ДЛЯ КНОПКИ СОГЛАСИЯ
@router.callback_query(lambda c: c.data == "accept_legal_rules")
async def accept_legal_handler(
    callback: types.CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    club: Club,
    club_id: int,
    club_settings: dict,
    is_super_admin: bool,
    is_owner: bool
):
    user_id = callback.from_user.id

    # Ставим True в базе данных
    await session.execute(
        update(User).where(User.user_id == user_id).values(is_accepted=True)
    )
    await session.commit()

    # Закрываем часики на кнопке и пишем уведомление
    await callback.answer("Условия успешно приняты! Добро пожаловать.")
    
    # Сносим юридическое сообщение с кнопками
    await callback.message.delete()

    # Отрисовываем меню без вызова start_handler
    welcome_text = club_settings.get("ui", {}).get("welcome_text") or "Добро пожаловать!"

    # Проверка на Админа / Владельца / Суперадмин
    if is_owner or is_super_admin:
        return await callback.message.answer(
            f"⚡ <b>Панель управления клуба «{club.name}»</b>\n\n{welcome_text}",
            reply_markup=admin_keyboard(club_settings=club_settings, club_id=club_id),
            parse_mode="HTML"
        )

    # Логика клиента (Ищем привязанного атлета по Telegram ID)
    stmt = select(Student).where(
        Student.parent_id == user_id,
        Student.club_id == club.id
    )
    student = (await session.execute(stmt)).scalar_one_or_none()

    if student:
        expire_str = student.expire_date.strftime('%d.%m.%Y') if student.expire_date else "не указано"
        status_text = f"✅ Атлет: <b>{student.name}</b>\n📅 Абонемент до: <b>{expire_str}</b>"

        await callback.message.answer(
            f"📍 <b>{club.name}</b>\n\n{welcome_text}\n\n{status_text}",
            reply_markup=get_profile_keyboard(club_settings=club_settings, is_authorized=True),
            parse_mode="HTML"
        )
    else:
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

        await callback.message.answer(
            f"📍 <b>{club.name}</b>\n\n{welcome_text}\n\n{status_text}",
            reply_markup=builder.as_markup(resize_keyboard=True, one_time_keyboard=True),
            parse_mode="HTML"
        )

