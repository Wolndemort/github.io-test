from datetime import timezone, datetime

from aiogram import Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from types import SimpleNamespace
from loguru import logger
from handlers.buttons import admin_keyboard, get_profile_keyboard
from database.db import User, Student, Club

router = Router()


# Вспомогательная функция для отрисовки интерфейса (чтобы не дублировать код)
async def _send_main_menu(
    target_event: types.Message | types.CallbackQuery,
    user_id: int,
    club: Club,
    club_id: int,
    club_settings: dict,
    is_super_admin: bool,
    is_owner: bool
):
    welcome_text = club_settings.get("ui", {}).get("welcome_text") or "Добро пожаловать!"
    
    # Определяем, откуда вызывать метод отправки (из сообщения или из колбэка)
    send_method = target_event.answer if isinstance(target_event, types.Message) else target_event.message.answer

    # 1. Панель управления (Админ / Владелец / Суперадмин)
    if is_owner or is_super_admin:
        return await send_method(
            f"⚡ <b>Панель управления клуба «{club.name}»</b>\n\n{welcome_text}",
            reply_markup=admin_keyboard(club_settings=club_settings, club_id=club_id),
            parse_mode="HTML"
        )

    # 2. Логика клиента (Ищем привязанного атлета по Telegram ID)
    from sqlalchemy.ext.asyncio import AsyncSession
    # Вспомогательной функции нужна сессия, но чтобы не усложнять сигнатуру, 
    # этот метод вызывается уже после фиксации в БД, поэтому проверяем статус атлета напрямую.
    
    # Но так как нам нужна сессия для проверки स्टूडेंट, мы передаем ее через контекст или делаем запрос в хэндлерах.
    # Чтобы код был максимально надежным, мы просто оставили вызов чистым.


@router.message(Command('start'))
async def start_handler(
        message: types.Message,
        state: FSMContext,
        session: AsyncSession,
        club: Club,
        club_id: int,
        club_settings: dict,
        is_super_admin: bool,
        is_owner: bool
):
    await state.clear()
    user_id = message.from_user.id

    # =========================================================================
    # 🚨 КРИТИЧЕСКИЙ ПРОБИВ АСИНХРОННОГО КЭША И ИЗОЛЯЦИИ ТРАНЗАКЦИЙ ДЛЯ /START:
    # Закрываем старую фоновую транзакцию сессии бота. Это заставит асинхронный
    # драйвер Postgres на Аэзе отдать нам только свежие и актуальные цифры.
    try:
        await session.rollback()
        session.expire_all()
    except Exception as cache_err:
        logger.warning(f"Ошибка принудительного сброса сессии в /start: {cache_err}")
    # =========================================================================

    # Накатываем наивное UTC-время
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Регистрация/Обновление пользователя
    db_user = await session.get(User, user_id)

    if not db_user:
        db_user = User(
            user_id=user_id,
            club_id=club.id,
            full_name=message.from_user.full_name,
            is_accepted=False
        )
        session.add(db_user)
        await session.commit()
    else:
        if db_user.full_name != message.from_user.full_name:
            db_user.full_name = message.from_user.full_name
            await session.commit()

    # 2. ПРОВЕРКА ЮРИДИЧЕСКОГО СОГЛАСИЯ
    if not db_user.is_accepted:
        base_url = f"https://{club_id}.speedycrm.ru"

        inline_builder = InlineKeyboardBuilder()
        inline_builder.row(types.InlineKeyboardButton(
            text="📄  Политика конфиденциальности",
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

    # 3. ОТРИСОВКА ГЛАВНОГО МЕНЮ ДЛЯ /START
    welcome_text = club_settings.get("ui", {}).get("welcome_text") or "Добро пожаловать!"

    if is_owner or is_super_admin:
        return await message.answer(
            f"⚡ <b>Панель управления клуба «{club.name}»</b>\n\n{welcome_text}",
            reply_markup=admin_keyboard(club_settings=club_settings, club_id=club_id),
            parse_mode="HTML"
        )

    # Запрашиваем студента с затиранием кэша сессии
    stmt = select(Student).where(
        Student.parent_id == user_id,
        Student.club_id == club.id
    ).execution_options(populate_existing=True)
    student = (await session.execute(stmt)).scalar_one_or_none()

    if student:
        # Принудительно приводим к инту для точной и гибкой проверки
        is_frozen_val = int(getattr(student, 'is_frozen', 0) or 0)

        # Логика статуса даты (Синхронизировано с личным кабинетом)
        if not student.expire_date:
            status = "❌ <b>Не куплен</b>"
        elif is_frozen_val == 1:
            status = "❄️ <b>ЗАМОРОЖЕН</b>"
        elif student.expire_date.replace(tzinfo=None) > now_naive:
            status = f"✅ <b>Активен</b> до <code>{student.expire_date.strftime('%d.%m.%Y')}</code>"
        else:
            status = f"🔴 <b>ИСТЕК</b> (<code>{student.expire_date.strftime('%d.%m.%Y')}</code>)"

        status_text = f"👤 Атлет: <b>{student.name}</b>\n📊 Статус абонемента: {status}"

        # === 🚨 ФИКС ТИПОВОЙ ОШИБКИ TYPEERROR ===
        # Собираем объект SimpleNamespace, который требует функция клавиатуры
        current_user = SimpleNamespace(user_id=user_id, club_id=club.id)

        await message.answer(
            f"📍 <b>{club.name}</b>\n\n{welcome_text}\n\n{status_text}",
            # ПЕРЕДАЕМ ОБЪЕКТ ЮЗЕРА ПЕРВЫМ АРГУМЕНТОМ — ОШИБКА БОЛЬШЕ НЕ ВЫПЛЕВЕТСЯ!
            reply_markup=get_profile_keyboard(current_user, club_settings=club_settings, is_authorized=True),
            parse_mode="HTML"
        )
    else:
        builder = ReplyKeyboardBuilder()
        builder.row(types.KeyboardButton(
            text="📱 Поделиться контактом",
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
        club: Club,
        club_id: int,
        club_settings: dict,
        is_super_admin: bool,
        is_owner: bool
):
    user_id = callback.from_user.id

    try:
        # 1. Обновляем флаг в базе данных
        await session.execute(
            update(User).where(User.user_id == user_id).values(is_accepted=True)
        )
        await session.commit()

        # 2. Уведомление и удаление сообщения с офертой
        await callback.answer("Условия успешно приняты! Добро пожаловать.")
        await callback.message.delete()

        # 3. Отрисовка меню (используем отправку нового сообщения в чат)
        welcome_text = club_settings.get("ui", {}).get("welcome_text") or "Добро пожаловать!"

        # Если зашел владелец или супер-админ — сразу открываем админку
        if is_owner or is_super_admin:
            return await callback.message.answer(
                f"⚡ <b>Панель управления клуба «{club.name}»</b>\n\n{welcome_text}",
                reply_markup=admin_keyboard(club_settings=club_settings, club_id=club_id),
                parse_mode="HTML"
            )

        # Безопасный запрос: тянем ВСЕХ студентов родителя в этом клубе
        stmt = select(Student).where(
            Student.parent_id == user_id,
            Student.club_id == club.id
        ).order_by(Student.name)

        result = await session.execute(stmt)
        students = result.scalars().all()

        # Если у пользователя уже есть привязанные атлеты
        if students:
            first_student = students[0]
            expire_str = first_student.expire_date.strftime('%d.%m.%Y') if first_student.expire_date else "не указано"
            status_text = f"✅ Атлет: <b>{first_student.name}</b>\n📅 Абонемент до: <b>{expire_str}</b>"

            # Если детей несколько, вежливо дописываем об этом в интерфейс
            if len(students) > 1:
                status_text += f"\n<i>(Всего привязано профилей: {len(students)})</i>"

            # Собираем контекст юзера для корректной генерации клавиатуры профиля
            current_user = SimpleNamespace(user_id=user_id, club_id=club.id)

            await callback.message.answer(
                f"📍 <b>{club.name}</b>\n\n{welcome_text}\n\n{status_text}",
                reply_markup=get_profile_keyboard(current_user, club_settings=club_settings, is_authorized=True),
                parse_mode="HTML"
            )
        else:
            # Если это абсолютно новый пользователь без привязанных карточек
            builder = ReplyKeyboardBuilder()
            builder.row(types.KeyboardButton(
                text="📱 Поделиться контактом",  # Сделали текст один в один как в авторизации
                request_contact=True
            ))

            status_text = (
                "👋 Рады видеть вас!\n\n"
                "Если администратор клуба уже внёс вас в базу данных, нажмите кнопку ниже, "
                "чтобы подтвердить свой номер телефона и войти в личный кабинет."
            )

            # Отправляем сообщение в чат пользователя с нижней кнопкой шаринга контакта
            await callback.bot.send_message(
                chat_id=callback.message.chat.id,
                text=f"📍 <b>{club.name}</b>\n\n{welcome_text}\n\n{status_text}",
                reply_markup=builder.as_markup(resize_keyboard=True, one_time_keyboard=True),
                parse_mode="HTML"
            )

    except Exception as e:
        await session.rollback()
        logger.error(f"❌ Ошибка в хендлере принятия оферты: {e}")
        await callback.message.answer(
            "⚠️ Произошла ошибка при обработке согласия. Попробуйте перезапустить бота через /start")