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
from admin_module.utils import is_staff_or_owner
from database.db import User, Student, Club
from services.audit import audit_event

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

    # Сбрасываем только незавершённую транзакцию. Не вызываем expire_all():
    # в async SQLAlchemy это может истечь объект Club из middleware, а
    # последующее обращение к club.id/name вызовет MissingGreenlet.
    try:
        await session.rollback()
    except Exception as cache_err:
        logger.warning(f"Ошибка сброса транзакции в /start: {cache_err}")

    # Исправлено: берем честное локальное время сервера без UTC сдвига,
    # чтобы оно идеально совпадало с логикой дат в вашей админ-панели
    now_naive = datetime.now().replace(tzinfo=None)

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
        await session.refresh(db_user)
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

    start_payload = ((message.text or "").split(maxsplit=1)[1].strip() if len((message.text or "").split(maxsplit=1)) > 1 else "")
    if start_payload.startswith("invite_"):
        raw_invite = start_payload.removeprefix("invite_").split("_")
        try:
            invited_student_id = int(raw_invite[0])
            invited_parent_slot = int(raw_invite[1]) if len(raw_invite) > 1 else 1
        except ValueError:
            invited_student_id = invited_parent_slot = 0
        invited_student = await session.get(Student, invited_student_id) if invited_student_id else None
        invited_phone = (invited_student.parent_phone if invited_parent_slot == 1 else invited_student.parent_phone_secondary) if invited_student else None
        if invited_student and invited_student.club_id == club.id and invited_phone:
            contact_keyboard = ReplyKeyboardBuilder()
            contact_keyboard.row(types.KeyboardButton(text="📱 Поделиться контактом", request_contact=True))
            return await message.answer(
                f"📍 Вас зарегистрировали в клубе <b>{club.name}</b> для атлета <b>{invited_student.name}</b>.\n\n"
                "Чтобы открыть личный кабинет, нажмите кнопку и подтвердите свой номер телефона.",
                reply_markup=contact_keyboard.as_markup(resize_keyboard=True, one_time_keyboard=False, is_persistent=True),
                parse_mode="HTML",
            )

    # 3. ОТРИСОВКА ГЛАВНОГО МЕНЮ ДЛЯ /START
    welcome_text = club_settings.get("ui", {}).get("welcome_text") or "Добро пожаловать!"

    if is_owner or is_super_admin:
        audit_event(
            "bot_menu_opened",
            club_id=club.id,
            actor_user_id=user_id,
            actor_role="super_admin" if is_super_admin and not is_owner else "owner",
            actor_name=message.from_user.full_name,
            action="open",
            object_type="menu",
            object_id="admin",
            location="bot/start",
        )
        return await message.answer(
            f"⚡ <b>Панель управления клуба «{club.name}»</b>\n\n{welcome_text}",
            reply_markup=admin_keyboard(
                club_settings=club_settings,
                club_id=club_id,
                subscription_date=club.subscription_expire_at
            ),
            parse_mode="HTML"
        )

    # Запрашиваем студента с затиранием кэша сессии
    stmt = select(Student).where(
        Student.parent_id == user_id,
        Student.club_id == club.id
    ).execution_options(populate_existing=True)
    # У одного родителя может быть несколько атлетов. Берём первого для
    # стартового экрана, а подробный список показывает отдельная кнопка.
    student = (await session.execute(stmt)).scalars().first()

    if student:
        is_frozen_val = int(getattr(student, 'is_frozen', 0) or 0)

        if not student.expire_date:
            status = "❌ <b>Не куплен</b>"
        elif is_frozen_val == 1:
            status = "❄️ <b>ЗАМОРОЖЕН</b>"
        elif student.expire_date.replace(tzinfo=None) > now_naive:
            status = f"✅ <b>Активен</b> до <code>{student.expire_date.strftime('%d.%m.%Y')}</code>"
        else:
            status = f"🔴 <b>ИСТЕК</b> (<code>{student.expire_date.strftime('%d.%m.%Y')}</code>)"

        status_text = f"👤 Атлет: <b>{student.name}</b>\n📊 Статус абонемента: {status}"

        # === ФИКС ТИПОВОЙ ОШИБКИ TYPEERROR ===
        current_user = SimpleNamespace(user_id=user_id, club_id=club.id)

        audit_event(
            "bot_profile_opened",
            club_id=club.id,
            actor_user_id=user_id,
            actor_role="client",
            actor_name=message.from_user.full_name,
            action="open",
            object_type="profile",
            object_id=user_id,
            location="bot/start",
            students=[s.id for s in (await session.execute(select(Student).where(Student.parent_id == user_id, Student.club_id == club.id))).scalars().all()],
            authorized=True,
        )
        await message.answer(
            f"📍 <b>{club.name}</b>\n\n{welcome_text}\n\n{status_text}",
            reply_markup=get_profile_keyboard(
                current_user,
                club.id,
                club_settings=club_settings,
                is_authorized=True,
                profile_mode="staff" if await is_staff_or_owner(session, club, user_id) else "client",
            ),
            parse_mode="HTML"
        )
    else:
        audit_event(
            "bot_profile_opened",
            club_id=club.id,
            actor_user_id=user_id,
            actor_role="client",
            actor_name=message.from_user.full_name,
            action="open",
            object_type="profile",
            object_id=user_id,
            location="bot/start",
            students=[],
            authorized=False,
        )
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
            reply_markup=builder.as_markup(resize_keyboard=True, one_time_keyboard=False, is_persistent=True),
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
                reply_markup=get_profile_keyboard(
                    current_user,
                    club.id,
                    club_settings=club_settings,
                    is_authorized=True,
                    profile_mode="staff" if await is_staff_or_owner(session, club, user_id) else "client",
                ),
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
                reply_markup=builder.as_markup(resize_keyboard=True, one_time_keyboard=False, is_persistent=True),
                parse_mode="HTML"
            )

    except Exception as e:
        await session.rollback()
        logger.error(f"❌ Ошибка в хендлере принятия оферты: {e}")
        await callback.message.answer(
            "⚠️ Произошла ошибка при обработке согласия. Попробуйте перезапустить бота через /start")
