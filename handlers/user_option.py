import qrcode
from types import SimpleNamespace
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
import io
from services.gate_control import process_athlete_gate_pass
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.filters import Command
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis
from database.db import Student, process_student_freeze, Club, User, VisitLog, PaymentOrder, Subscription
from services.abuse_guard import rate_limit, audit_block
from config import secret_key
from sqlalchemy import select
from sqlalchemy import func
from handlers.buttons import get_main_menu_keyboard, get_profile_keyboard, get_section_menu_kb
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, BufferedInputFile
from datetime import timedelta, timezone
from aiogram import Router, F, types
from aiogram.exceptions import TelegramBadRequest
from datetime import datetime
from loguru import logger
import hashlib
import hmac
from handlers.states import RegistrationStates, PaymentStates
from handlers.skud import trigger_dingtian_turnstile


def generate_signature(user_id, time_salt):
    msg = f"{user_id}:{time_salt}".encode()
    signature = hmac.new(
        secret_key.encode(),
        msg,
        hashlib.sha256
    ).hexdigest()
    return signature[:10]


def _default_student_discipline(club_settings: dict) -> str:
    disciplines = club_settings.get("disciplines", {})
    if isinstance(disciplines, dict) and disciplines:
        active_codes = [
            code
            for code, info in disciplines.items()
            if isinstance(info, dict) and info.get("active")
        ]
        if active_codes:
            return active_codes[0]
        return next(iter(disciplines.keys()))
    return "boxing"


router = Router()


@router.callback_query(F.data == 'begin')
async def go_to_begin(
        callback: types.CallbackQuery,
        state: FSMContext,
        club: Club,
        club_settings: dict
):
    await state.clear()
    user_name = callback.from_user.first_name

    # 1. Сначала пытаемся взять имя из JSON-настроек UI
    setting_name = club_settings.get("ui", {}).get("club_name")

    # 2. Если имени в UI нет ИЛИ там зашита дефолтная заглушка — берем реальное имя клуба из club.name
    if not setting_name or setting_name == "Новый фитнес-клуб":
        club_name = club.name
    else:
        club_name = setting_name

    await callback.message.edit_text(
        text=f"<b>{club_name}</b>\n\nС возвращением, {user_name}! Чем я могу помочь?",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(club_settings, club.id)
    )
    await callback.answer()


@router.message(Command('profile', 'check_status_now'))
@router.callback_query(F.data.in_(['profile', 'check_status_now']))
async def universal_profile_handler(
        event: types.Message | types.CallbackQuery,
        session: AsyncSession,
        club: Club,
        club_settings: dict,
        club_id: int
):
    user_id = event.from_user.id

    # Приводим время к наивному UTC
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)

    # Запрашиваем студентов этого родителя для текущего клуба
    stmt = select(Student).where(
        Student.parent_id == user_id,
        Student.club_id == club_id
    ).order_by(Student.name).execution_options(populate_existing=True)

    result = await session.execute(stmt)
    students = result.scalars().all()

    is_auth = bool(students)

    if not is_auth:
        status_text = (
            f"📍 Клуб: <b>{club.name}</b>\n\n"
            "⚠️ <b>У вас еще нет привязанных атлетов в этом клубе.</b>\n\n"
            "Нажмите <b>«Привязать профиль»</b> или добавьте атлета вручную."
        )
    else:
        status_text = f"🏰 Клуб: <b>{club.name}</b>\n🆔 <b>Ваши профили:</b>\n"
        for s in students:
            is_frozen_val = int(getattr(s, 'is_frozen', 0) or 0)

            # Логика статуса даты
            if not s.expire_date:
                status = "❌ <b>Не куплен</b>"
            elif is_frozen_val == 1:
                status = "❄️ <b>ЗАМОРОЖЕН</b>"
            elif s.expire_date.replace(tzinfo=None) > now_naive:
                status = f"✅ <b>Активен</b> до <code>{s.expire_date.strftime('%d.%m.%Y')}</code>"
            else:
                status = f"🔴 <b>ИСТЕК</b> (<code>{s.expire_date.strftime('%d.%m.%Y')}</code>)"

            # Логика баланса
            balance = getattr(s, 'balance_lessons', 0)
            if balance >= 900:
                lessons_info = "♾ <b>Безлимит</b>"
            elif balance > 0:
                lessons_info = f"🔢 Занятий: <b>{balance}</b>"
            else:
                lessons_info = "❌ <b>Занятия окончены</b>"

            status_text += f"\n• <b>{s.name}</b>: {status}\n  └ {lessons_info}"

    final_text = (
        f"👤 <b>Личный кабинет</b>\n\n{status_text}\n\n"
        "<i>Используйте кнопки ниже для управления:</i>"
    )
    current_user = SimpleNamespace(user_id=user_id, club_id=club.id)
    reply_markup = get_profile_keyboard(current_user, club_settings=club_settings, is_authorized=is_auth)

    # Переносим event.answer() в самое начало блока отправки,
    # чтобы кнопка отжималась моментально в 100% случаев, предотвращая зависание UI
    if isinstance(event, types.CallbackQuery):
        try:
            await event.answer()
        except Exception as ans_err:
            logger.debug(f"Не удалось ответить на колбэк: {ans_err}")

    try:
        if isinstance(event, types.CallbackQuery):
            await event.message.edit_text(
                text=final_text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        else:
            await event.answer(
                text=final_text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )

    except Exception as e:
        # Если вылез варнинг о том, что текст совпал — игнорируем, интерфейс уже стабилен
        if "message is not modified" in str(e):
            pass
        else:
            logger.error(f"❌ Ошибка рендеринга профиля: {e}", exc_info=True)


@router.callback_query(F.data == 'detailed_status_info')
async def detailed_status_handler(
        callback: types.CallbackQuery,
        session: AsyncSession,
        club: Club
):
    user_id = callback.from_user.id

    # Сервер на Аэзе живет по UTC, оставляем сравнение для логики в UTC формате
    now = datetime.now(timezone.utc)

    # Запрашиваем студентов этого родителя для текущего клуба
    stmt = select(Student).where(
        Student.parent_id == user_id,
        Student.club_id == club.id
    ).order_by(Student.name)

    result = await session.execute(stmt)
    students = result.scalars().all()

    if not students:
        await callback.answer("⚠️ У вас нет привязанных атлетов для просмотра деталей.", show_alert=True)
        return

    # Заголовок нового экрана
    detail_text = f"📊 <b>Статус атлетов</b>\n🏰 Клуб: <b>{club.name}</b>\n\n"

    # Вытаскиваем таймаут сессии клуба из JSONB (дефолт 150 минут)
    club_settings = club.club_settings or {}
    timeout_minutes = club_settings.get("limits", {}).get("session_timeout_minutes", 150)

    for s in students:
        # 1. Расчет дней до окончания
        if s.expire_date:
            expire_naive = s.expire_date.replace(tzinfo=None)
            now_naive = now.replace(tzinfo=None)

            days_left = (expire_naive - now_naive).days
            if days_left >= 0:
                time_info = f"⏳ Осталось дней: <b>{days_left}</b>"
            else:
                time_info = f"🗓 Истёк: <b>{abs(days_left)} дн. назад</b>"
        else:
            time_info = "⏳ Срок действия: <b>не установлен</b>"

        # 2. Форматирование даты последнего визита И РАСЧЕТ ТЕКУЩЕЙ СЕССИИ
        session_status_str = ""

        # ИСПРАВЛЕНО: Строгая проверка на существование даты и на реальный год (защита от багов СУБД)
        if s.last_visit is not None and getattr(s.last_visit, 'year', 0) > 2000:
            last_visit_utc = s.last_visit.replace(tzinfo=timezone.utc) if s.last_visit.tzinfo is None else s.last_visit

            # Прибавляем +3 часа к UTC базы для красивого вывода МСК родителю!
            last_visit_moscow = last_visit_utc.replace(tzinfo=None) + timedelta(hours=3)
            last_visit_str = f"<b>{last_visit_moscow.strftime('%d.%m.%Y в %H:%M')}</b>"

            # Сравнение идет в строгом серверном UTC (как в кроне)
            if now - last_visit_utc < timedelta(minutes=timeout_minutes):
                # Время окончания сессии на экране тоже сдвигаем на +3 часа!
                session_end_moscow = last_visit_moscow + timedelta(minutes=timeout_minutes)
                session_end_str = session_end_moscow.strftime("%H:%M")
                session_status_str = f"🚪 Сессия входа: <b>🟢 Активна (до {session_end_str})</b>\n"
            else:
                session_status_str = f"🚪 Сессия входа: <b>⚫️ Завершена</b>\n"
        else:
            # Гарантированный вывод для абсолютно новых учеников
            last_visit_str = "<i>еще не посещал занятия</i>"
            session_status_str = f"🚪 Сессия входа: <b>⚫️ Нет активных сессий</b>\n"

        # 3. Форматирование дня рождения
        if s.birthday:
            birthday_str = f"<b>{s.birthday.strftime('%d.%m.%Y')}</b>"
        else:
            birthday_str = "<i>не указан</i>"

        # 4. Логика возможности заморозки
        freeze_status = "✅ Доступна" if s.can_freeze == 1 else "❌ Недоступна"

        # Собираем блок информации
        detail_text += (
            f"👤 Атлет: <b>{s.name}</b>\n"
            f"🆔 ID профиля: <code>{s.id}</code>\n"
            f"🎂 День рождения: {birthday_str}\n"
            f"{time_info}\n"
            f"❄️ Возможность заморозки: {freeze_status}\n"
            f"👟 Последний визит: {last_visit_str}\n"
            f"{session_status_str}"
            f"───────────────────\n\n"
        )

    visit_count_stmt = select(func.count(VisitLog.id)).where(
        VisitLog.club_id == club.id,
        VisitLog.student_id.in_([s.id for s in students])
    )
    total_visits = await session.scalar(visit_count_stmt) or 0

    recent_visits_stmt = (
        select(VisitLog, Student.name)
        .join(Student, Student.id == VisitLog.student_id)
        .where(
            VisitLog.club_id == club.id,
            VisitLog.student_id.in_([s.id for s in students])
        )
        .order_by(VisitLog.visited_at.desc())
        .limit(5)
    )
    recent_visits_rows = (await session.execute(recent_visits_stmt)).all()

    visits_block = f"📚 <b>История посещений клуба:</b>\nВсего чек-инов: <b>{total_visits}</b>\n"
    if recent_visits_rows:
        visits_block += "\n<b>Последние 5:</b>\n"
        for visit, student_name in recent_visits_rows:
            visit_time = visit.visited_at.strftime("%d.%m.%Y %H:%M")
            visits_block += f"• <b>{student_name}</b> — <code>{visit_time}</code>\n"
    else:
        visits_block += "\n<i>Пока нет ни одного чекина.</i>\n"

    detail_text += visits_block

    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад в Личный Кабинет", callback_data="profile")]
    ])

    try:
        await callback.message.edit_text(
            text=detail_text,
            reply_markup=back_keyboard,
            parse_mode="HTML"
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"❌ Ошибка в детальном статусе: {e}")
        await callback.answer("Ошибка при загрузке подробных данных")


@router.callback_query(F.data == "payment_history")
async def payment_history_handler(
        callback: types.CallbackQuery,
        session: AsyncSession,
        club: Club
):
    user_id = callback.from_user.id
    students = (await session.execute(
        select(Student).where(Student.parent_id == user_id, Student.club_id == club.id).order_by(Student.name)
    )).scalars().all()

    if not students:
        return await callback.answer("У вас нет привязанных атлетов.", show_alert=True)

    student_ids = [s.id for s in students]
    orders = (await session.execute(
        select(PaymentOrder)
        .where(PaymentOrder.club_id == club.id, PaymentOrder.student_id.in_(student_ids))
        .order_by(PaymentOrder.created_at.desc())
        .limit(10)
    )).scalars().all()
    subs = (await session.execute(
        select(Subscription)
        .where(Subscription.club_id == club.id, Subscription.user_id == user_id)
        .order_by(Subscription.created_at.desc())
        .limit(5)
    )).scalars().all()

    text = f"🧾 <b>История</b>\n🏰 Клуб: <b>{club.name}</b>\n\n"
    if orders:
        text += "<b>Последние оплаты:</b>\n"
        for order in orders:
            created = order.created_at.strftime("%d.%m.%Y %H:%M") if order.created_at else "—"
            amount = f"{order.amount_kopecks / 100:.0f} ₽"
            text += f"• <code>{created}</code> — <b>{amount}</b> — {order.status}\n"
    else:
        text += "<i>Оплат пока нет.</i>\n"

    text += "\n"
    if subs:
        text += "<b>Подписки:</b>\n"
        for sub in subs:
            next_charge = sub.next_charge_at.strftime("%d.%m.%Y") if sub.next_charge_at else "—"
            text += f"• Атлет ID <code>{sub.student_id}</code> — след. списание: <b>{next_charge}</b> — {'активна' if sub.is_active else 'неактивна'}\n"
    else:
        text += "<i>Подписок пока нет.</i>\n"

    text += "\n<b>Ваши атлеты:</b>\n"
    for s in students:
        status = "❄️ заморожен" if s.is_frozen else ("✅ активен" if s.expire_date and s.expire_date > datetime.now() else "🔴 истек")
        text += f"• <b>{s.name}</b> — {status}\n"

    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад в профиль", callback_data="profile")]
    ])
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith('section_'))
async def universal_section_handler(
        callback: types.CallbackQuery,
        club_settings: dict  # Наш конфиг из мидлвари
):
    # 1. Достаем код секции из колбэка (например, 'bjj' или 'kids')
    section_code = callback.data.split('_')[1]

    # 2. Ищем данные именно этой секции в конфиге клуба
    discipline = club_settings.get("disciplines", {}).get(section_code)

    if not discipline or not discipline.get("active"):
        return await callback.answer("Секция временно недоступна", show_alert=True)

    # 3. Формируем текст (берем имя из конфига)
    # Можно добавить поле "description" в конфиг, чтобы тексты были уникальные
    name = discipline.get("name", section_code.upper())
    desc = discipline.get("description", "Информация о направлении:")

    await callback.message.edit_text(
        text=f"🥋 <b>{name}</b>\n\n{desc}",
        parse_mode="HTML",
        # Вызываем универсальную клаву раздела (Цены/Расписание/Купить)
        reply_markup=get_section_menu_kb(section_code, name)
    )
    await callback.answer()


@router.callback_query(F.data.startswith('price_'))
async def universal_price_handler(callback: types.CallbackQuery, club_settings: dict):
    code = callback.data.split('_')[1]
    discipline = club_settings.get("disciplines", {}).get(code)

    if not discipline:
        return await callback.answer("Данные о ценах не найдены")

    name = discipline.get("name", code.upper())
    price_text = f"💰 <b>Стоимость абонементов {name}:</b>\n\n"

    # 1. Обработка безлимита
    if discipline.get("type") == "unlimited":
        price = discipline.get('price', 'Не указана')
        price_text += f"• Безлимит — {price}₽"

    # 2. Обработка тарифов по занятиям
    else:
        tariffs = discipline.get("tariffs", [])
        if not tariffs:
            price_text += "Тарифы пока не добавлены."

        for t in tariffs:
            count = t.get('count', '?')
            price = t.get('price', '—')
            days = t.get('days')

            # Если дни есть — пишем (30 дн.), если нет — просто пропускаем этот кусок
            days_info = f" ({days} дн.)" if days else ""
            price_text += f"• {count} зан. — {price}₽{days_info}\n"

    await callback.message.edit_text(
        text=price_text,
        parse_mode='HTML',
        reply_markup=get_section_menu_kb(code, name)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_freeze_"))
async def finalize_freeze_action(
    callback: types.CallbackQuery,
    session: AsyncSession,
    club_settings: dict,
    club: Club
):
    try:
        # Парсим ID студента из колбэка
        student_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        return await callback.answer("❌ Ошибка формата ID", show_alert=True)

    # Достаем шаг заморозки из конфига клуба
    freeze_days = club_settings.get("limits", {}).get("freeze_days_step", 7)

    # ИСПРАВЛЕНО: Порядок аргументов строго соответствует функции бд
    new_date = await process_student_freeze(
        student_id=student_id,
        club_id=club.id,
        club_settings=club_settings,
        session=session,
        days=freeze_days
    )

    # Добавили проверку на отключенный функционал в самом клубе
    if new_date == "disabled":
        return await callback.answer("🚫 В вашем клубе функция заморозки отключена в настройках!", show_alert=True)

    elif new_date:
        formatted_date = new_date.strftime('%d.%m.%Y')
        await callback.answer("✅ Абонемент заморожен!", show_alert=True)

        await callback.message.edit_text(
            text=(
                f"✅ <b>Заморозка выполнена успешно!</b>\n\n"
                f"Абонемент продлен на <b>{freeze_days} дней</b>.\n"
                f"Новая дата окончания: <b>{formatted_date}</b>\n\n"
                f"❄️ <i>Статус: Заморожен (разморозится автоматически при входе)</i>"
            ),
            parse_mode="HTML"
        )
    else:
        await callback.answer(
            "❌ Заморозка недоступна.\n"
            "Лимит исчерпан, абонемент просрочен или уже заморожен.",
            show_alert=True
        )


@router.callback_query(F.data == "freeze_sub")
async def choose_student_for_freeze(
        callback: types.CallbackQuery,
        session: AsyncSession,
        club: Club,  # Из Middleware
        club_settings: dict  # Из Middleware
):
    user_id = callback.from_user.id

    # 1. Тянем атлетов ТОЛЬКО этого родителя и ТОЛЬКО этого клуба
    stmt = select(Student).where(
        Student.parent_id == user_id,
        Student.club_id == club.id
    ).order_by(Student.name)

    result = await session.execute(stmt)
    students = result.scalars().all()

    if not students:
        return await callback.answer("У вас нет атлетов в этом клубе!", show_alert=True)

    # 2. Достаем настройки из SaaS-конфига (Limits -> freeze_days_step)
    freeze_days = club_settings.get("limits", {}).get("freeze_days_step", 7)

    builder = InlineKeyboardBuilder()
    count = 0
    now = datetime.now()

    for s in students:
        # Проверка: Абонемент активен (дата > сегодня) И есть лимит заморозок (can_freeze > 0)
        # И абонемент НЕ заморожен прямо сейчас (is_frozen != 1)
        if s.expire_date and s.expire_date > now and getattr(s, 'can_freeze', 0) > 0 and getattr(s, 'is_frozen',
                                                                                                 0) == 0:
            builder.row(types.InlineKeyboardButton(
                text=f"❄️ {s.name}",
                callback_data=f"confirm_freeze_{s.id}"
            ))
            count += 1

    if count == 0:
        return await callback.answer(
            "Нет активных абонементов, доступных для заморозки!",
            show_alert=True
        )

    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="profile"))

    # 3. Текст с динамическим числом дней из конфига
    await callback.message.edit_text(
        text=(
        f"❄️ <b>Заморозка</b>\n\n"
        f"Клуб: <b>{club.name}</b>\n"
        f"Выберите атлета. Срок действия будет продлен на <b>{freeze_days} дней</b>.\n\n"
            f"⚠️ <i>Заморозка доступна 1 раз за период. Абонемент разморозится автоматически при первом входе в зал.</i>"
        ),
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "buy_freeze")
async def choose_student_for_paid_freeze(callback: types.CallbackQuery, session: AsyncSession, club: Club,
                                          club_settings: dict, state: FSMContext, redis: Redis):
    ok = await rate_limit(redis, f"rl:buy_freeze_menu:{club.id}:{callback.from_user.id}", 5, 60)
    if not ok:
        await audit_block("ui_rate_limited", "buy_freeze_menu", club_id=club.id, user_id=callback.from_user.id)
        return await callback.answer("Слишком часто. Попробуйте через минуту.", show_alert=True)
    price = club_settings.get("limits", {}).get("freeze_price_per_day", 0)
    if price <= 0:
        return await callback.answer("Покупка заморозки сейчас недоступна.", show_alert=True)
    result = await session.execute(select(Student).where(
        Student.parent_id == callback.from_user.id, Student.club_id == club.id
    ).order_by(Student.name))
    students = result.scalars().all()
    builder = InlineKeyboardBuilder()
    now = datetime.now()
    available = 0
    for student in students:
        if student.expire_date and student.expire_date > now and not getattr(student, "is_frozen", 0):
            builder.row(types.InlineKeyboardButton(text=f"❄️ {student.name}", callback_data=f"paid_freeze_student_{student.id}"))
            available += 1
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="profile"))
    if not available:
        return await callback.answer("Нет активных абонементов для заморозки.", show_alert=True)
    await callback.message.edit_text(
        f"❄️ <b>Покупка заморозки</b>\n\nВыберите атлета, затем введите количество дней.\n"
        f"Стоимость: <b>{price} ₽ за 1 день</b>", reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("paid_freeze_student_"))
async def start_paid_freeze_days(callback: types.CallbackQuery, state: FSMContext, club_settings: dict, redis: Redis):
    ok = await rate_limit(redis, f"rl:buy_freeze_pick:{callback.from_user.id}", 5, 60)
    if not ok:
        await audit_block("ui_rate_limited", "buy_freeze_pick", user_id=callback.from_user.id)
        return await callback.answer("Слишком часто. Попробуйте позже.", show_alert=True)
    student_id = int(callback.data.rsplit("_", 1)[1])
    price = club_settings.get("limits", {}).get("freeze_price_per_day", 0)
    await state.update_data(student_id=student_id, payment_kind="FREEZE", freeze_price_per_day=price)
    await state.set_state(PaymentStates.waiting_for_freeze_days)
    await callback.message.edit_text(
        f"Введите количество дней заморозки (от 1 до 365).\n"
        f"Цена: <b>{price} ₽ за день</b>", parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == 'show_qr')
async def choose_student_for_qr(
        callback: types.CallbackQuery,
        session: AsyncSession,
        club: Club,  # Из мидлвари
        club_settings: dict  # Из мидлвари
):
    # 1. Проверяем, включен ли QR-модуль в этом конкретном клубе
    if not club_settings.get("features", {}).get("qr_checkin", True):
        return await callback.answer("❌ QR-пропуски временно отключены в этом клубе.", show_alert=True)

    user_id = callback.from_user.id

    # 2. Изоляция: тянем студентов ТОЛЬКО этого родителя и ТОЛЬКО этого клуба
    stmt = select(Student).where(
        Student.parent_id == user_id,
        Student.club_id == club.id
    ).order_by(Student.name)

    result = await session.execute(stmt)
    students = result.scalars().all()

    if not students:
        return await callback.answer(f"В клубе {club.name} у вас еще нет атлетов!", show_alert=True)

    # 3. Собираем клавиатуру
    builder = InlineKeyboardBuilder()
    for s in students:
        # Можно добавить статус (активен/истек) прямо в текст кнопки
        builder.row(InlineKeyboardButton(
            text=f"🪪 {s.name}",
            callback_data=f"gen_qr_{s.id}")
        )

    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="profile"))

    await callback.message.edit_text(
        text=f"📍 Клуб: <b>{club.name}</b>\n\nВыберите атлета, для которого нужно сформировать QR-код пропуска:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()


LAYOUT_MAP = str.maketrans(
    "йцукенгшщзхъфывапролджэячсмитьбю.ЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ,",
    "qwertyuiop[]asdfghjkl;'zxcvbnm,./QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>?"
)


def fix_layout(text: str) -> str:
    if any(c in "фывапрол" for c in text.lower()):
        return text.translate(LAYOUT_MAP)
    return text


async def _handle_qr_scan_data(
    raw_data: str,
    message: types.Message,
    session: AsyncSession,
    club: Club,
    club_settings: dict,
):
    raw_data = fix_layout(raw_data)
    parts = raw_data.strip().split(':')
    if len(parts) != 4 or parts[0] != 'student':
        return await message.answer("❌ Ошибка: Неверный формат QR")

    try:
        scanned_id = int(parts[1])
    except ValueError:
        return await message.answer("❌ Ошибка: Неверный ID в QR")

    # Подпись нельзя игнорировать: иначе любой пользователь может подставить ID
    # любого атлета вручную. Сравнение через compare_digest защищает от timing-атак.
    time_salt, provided_signature = parts[2], parts[3]
    try:
        qr_time = datetime.strptime(time_salt, "%Y-%m-%d-%H").replace(tzinfo=timezone.utc)
        qr_age = (datetime.now(timezone.utc) - qr_time).total_seconds()
        if qr_age < -300 or qr_age > 2 * 60 * 60:
            return await message.answer("❌ Срок действия QR-пропуска истёк. Сформируйте новый QR.")
    except ValueError:
        return await message.answer("❌ Ошибка: срок действия QR невозможно проверить")
    expected_signature = generate_signature(scanned_id, time_salt)
    if not hmac.compare_digest(provided_signature, expected_signature):
        return await message.answer("❌ Ошибка: Недействительный QR-пропуск")

    res = await process_athlete_gate_pass(
        scanned_id,
        session,
        club_settings,
        expected_club_id=club.id,
    )
    if not res["success"]:
        return await message.answer(res["message"])

    freeze_notice = (
        f"\n\u2744\ufe0f \u0414\u043e\u0441\u0440\u043e\u0447\u043d\u0430\u044f \u0440\u0430\u0437\u043c\u043e\u0440\u043e\u0437\u043a\u0430! \u0421\u0434\u0432\u0438\u0433 \u043d\u0430 -{res['returned_early_days']} \u0434\u043d."
        if res["is_was_frozen"] and res["returned_early_days"] > 0
        else ""
    )

    await message.answer(
        f"\U0001f7e2 <b>\u041f\u0420\u041e\u0425\u041e\u0414\u0418\u0422\u0415</b>\n\U0001f464 \u0410\u0442\u043b\u0435\u0442: <b>{res['student_name']}</b>\n"
        f"\U0001f4c9 {res['message']}\n\U0001f4c6 \u0414\u043e: {res['expire_str']}{freeze_notice}\n{res['turnstile_status']}",
        parse_mode='HTML'
    )

    if res["parent_id"]:
        try:
            await message.bot.send_message(
                chat_id=int(res["parent_id"]),
                text=f"❗ <b>{res['club_name']}</b>: {res['student_name']} вошел в зал.",
                parse_mode="HTML"
            )
        except Exception:
            pass


@router.message(F.web_app_data)
async def parse_qr_scan(message: types.Message, session: AsyncSession, club: Club, club_settings: dict):
    await _handle_qr_scan_data(
        raw_data=message.web_app_data.data,
        message=message,
        session=session,
        club=club,
        club_settings=club_settings
    )


@router.message(F.text.startswith(("student", "ыегвуте")))
async def manual_scanner_handler(
        message: types.Message,
        session: AsyncSession,  # ÐÑÐ¸Ð»ÐµÑÐµÐ»Ð¾ Ð¸Ð· Ð¼Ð¸Ð´Ð»Ð²Ð°ÑÐ¸
        club: Club,  # ÐÑÐ¸Ð»ÐµÑÐµÐ»Ð¾ Ð¸Ð· Ð¼Ð¸Ð´Ð»Ð²Ð°ÑÐ¸
        club_settings: dict  # ÐÑÐ¸Ð»ÐµÑÐµÐ»Ð¾ Ð¸Ð· Ð¼Ð¸Ð´Ð»Ð²Ð°ÑÐ¸
):
    await _handle_qr_scan_data(
        raw_data=message.text,
        message=message,
        session=session,
        club=club,
        club_settings=club_settings
    )

@router.callback_query(F.data.startswith("gen_qr_"))
async def handle_gen_qr(
        callback: types.CallbackQuery,
        session: AsyncSession,
        club: Club  # Из мидлвари
):
    student_id = int(callback.data.split("_")[-1])

    # 1. Тянем имя атлета из БД (чтобы написать в подписи)
    student = await session.get(Student, student_id)
    if not student or student.club_id != club.id:
        return await callback.answer("❌ Ошибка: атлет не найден!", show_alert=True)

    # 2. Генерация данных (твоя логика с HMAC)
    now = datetime.now(timezone.utc)
    time_salt = now.strftime('%Y-%m-%d-%H')
    signature = generate_signature(student_id, time_salt)
    qr_data = f"student:{student_id}:{time_salt}:{signature}"

    # 3. Генерация самой картинки
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(qr_data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    io_bytes = io.BytesIO()
    img.save(io_bytes, "PNG")
    io_bytes.seek(0)

    photo = BufferedInputFile(io_bytes.getvalue(), filename=f"qr_{student_id}.png")

    # 4. Красивый ответ с кнопкой "Назад"
    # В блоке 4 подменяем callback_data
    kb = InlineKeyboardBuilder()
    # Зашиваем префикс del_photo и возвращаем админа/родителя в профиль
    kb.row(types.InlineKeyboardButton(text="🔙 В профиль", callback_data="back_profile_del"))


    await callback.message.answer_photo(
        photo=photo,
        caption=(
            f"🎫 <b>QR-пропуск: {student.name}</b>\n"
            f"🏛 Клуб: <b>{club.name}</b>\n\n"
            f"Покажите этот код администратору или поднесите к сканеру.\n"
            f"⚠️ <i>Код обновляется каждый час.</i>"
        ),
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "back_profile_del")
async def back_to_profile_and_delete_qr(
        callback: types.CallbackQuery,
        club: Club,
        club_settings: dict,
):
    await callback.answer()

    # 1. Жестко удаляем сообщение с картинкой QR-кода
    try:
        await callback.message.delete()
    except Exception:
        pass

    # 2. Отправляем новое меню профиля. Нельзя вручную вызывать другой
    # aiogram-handler: dispatcher не передаёт ему внутренние handler/data.
    await callback.message.answer(
        "Возвращаемся в профиль 👇",
        reply_markup=get_main_menu_keyboard(club_settings, club.id),
    )


@router.callback_query(F.data == "add_athlete")
async def start_add_athlete(callback: types.CallbackQuery, state: FSMContext, club: Club):
    await state.clear()  # Сбрасываем старое на всякий случай
    # Сохраняем ID клуба в стейт
    await state.update_data(current_club_id=club.id)

    await callback.message.answer(
        f"📍 Регистрация в клубе: <b>{club.name}</b>\n\n"
        f"Введите <b>Имя и Фамилия</b> атлета (себя или ребенка):",
        parse_mode="HTML"
    )
    await state.set_state(RegistrationStates.waiting_for_name)
    await callback.answer()


@router.callback_query(F.data == "edit_birthday")
async def choose_birthday_edit_student(callback: types.CallbackQuery, session: AsyncSession, club: Club):
    students = (await session.execute(
        select(Student).where(Student.parent_id == callback.from_user.id, Student.club_id == club.id).order_by(Student.name)
    )).scalars().all()
    if not students:
        return await callback.answer("У вас пока нет атлетов в этом клубе.", show_alert=True)
    builder = InlineKeyboardBuilder()
    for student in students:
        current = student.birthday.strftime("%d.%m.%Y") if student.birthday else "не указана"
        builder.row(types.InlineKeyboardButton(
            text=f"{student.name} · {current}", callback_data=f"edit_birthday_student_{student.id}"
        ))
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="profile"))
    await callback.message.edit_text("Выберите атлета для указания даты рождения:", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("edit_birthday_student_"))
async def start_birthday_edit(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, club: Club):
    try:
        student_id = int(callback.data.rsplit("_", 1)[1])
    except ValueError:
        return await callback.answer("Некорректный атлет.", show_alert=True)
    student = await session.get(Student, student_id)
    if not student or student.club_id != club.id or student.parent_id != callback.from_user.id:
        return await callback.answer("Атлет не найден.", show_alert=True)
    await state.update_data(edit_birthday_student_id=student_id)
    await state.set_state(RegistrationStates.waiting_for_birthday_edit)
    await callback.message.edit_text(
        f"Атлет: <b>{student.name}</b>\n\nВведите дату в формате ДД.ММ.ГГГГ или отправьте 0, чтобы удалить дату:",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(RegistrationStates.waiting_for_birthday_edit)
async def save_birthday_edit(message: types.Message, state: FSMContext, session: AsyncSession):
    value = (message.text or "").strip()
    birthday = None
    if value != "0":
        try:
            birthday = datetime.strptime(value, "%d.%m.%Y").date()
            if birthday > datetime.now().date() or birthday.year < 1900:
                raise ValueError
        except ValueError:
            return await message.answer("❌ Введите корректную дату ДД.ММ.ГГГГ или 0 для удаления.")
    data = await state.get_data()
    student = await session.get(Student, data.get("edit_birthday_student_id"), with_for_update=True)
    if not student or student.parent_id != message.from_user.id:
        await state.clear()
        return await message.answer("❌ Атлет не найден или доступ запрещён.")
    student.birthday = birthday
    await session.commit()
    await state.clear()
    await message.answer("✅ Дата рождения сохранена." if birthday else "✅ Дата рождения удалена.")


# ШАГ 2: Поймали имя -> Запрашиваем ДЕНЬ РОЖДЕНИЯ
@router.message(RegistrationStates.waiting_for_name)
async def process_athlete_name(message: types.Message, state: FSMContext):
    # Требуем, чтобы ввели минимум два слова (Имя и Фамилия), как ты просил в ТЗ!
    parts = message.text.strip().split()
    if len(parts) < 2:
        return await message.answer(
            "⚠️ Пожалуйста, укажите именно <b>Имя и Фамилию</b> через пробел (например: Иван Иванов):",
            parse_mode="HTML"
        )

    await state.update_data(athlete_name=message.text.strip())

    # Переключаем на новый шаг ввода ДР
    await state.set_state(RegistrationStates.waiting_for_birthday)
    await message.answer(
        f"✅ Имя сохранено: <b>{message.text}</b>\n\n"
        f"Введите <b>день рождения атлета</b> в формате ДД.ММ.ГГГГ\n"
        f"<i>(например: 15.08.2012 или напишите '0', если неизвестно):</i>",
        parse_mode="HTML"
    )


# ШАГ 3: Поймали ДР -> Валидируем и сохраняем в базу PostgreSQL
@router.message(RegistrationStates.waiting_for_birthday)
async def process_athlete_birthday(
        message: types.Message,
        state: FSMContext,
        session: AsyncSession,
        club: Club,
        club_settings: dict
):
    birthday_text = message.text.strip()
    birthday_date = None
    if birthday_text != "0":
        try:
            # Валидируем формат даты
            birthday_date = datetime.strptime(birthday_text, "%d.%m.%Y").date()
        except ValueError:
            return await message.answer(
                "❌ Неверный формат! Введите дату строго в формате ДД.ММ.ГГГГ (например: 25.10.2015)\n"
                "Или напишите '0', если дата рождения неизвестна:"
            )

    # Достаем данные из стейта
    data = await state.get_data()
    name = data.get('athlete_name')

    # ИСПРАВЛЕНО: Берем club_id ТОЛЬКО из стейта.
    # Если там пусто, используем inspect или берём из __dict__ объекта club, чтобы не триггерить lazy load
    club_id = data.get('current_club_id') or club.__dict__.get('id')
    user_id = message.from_user.id

    # Берем имя родителя из ТГ, чтобы full_name в таблице users не был NULL
    user_full_name = message.from_user.full_name or "Не указано"

    try:
        # 1. ПРОВЕРКА / АВТОПРИСУТСТВИЕ РОДИТЕЛЯ В ТАБЛИЦЕ USERS
        user_stmt = select(User).where(User.user_id == user_id).with_for_update()
        user_exists = (await session.execute(user_stmt)).scalar_one_or_none()

        if not user_exists:
            new_user = User(
                user_id=user_id,
                club_id=club_id,
                full_name=user_full_name,
                is_accepted=False,
                is_biometric_enabled=False
            )
            session.add(new_user)
            await session.flush()  # Фиксируем родителя для ForeignKey
        else:
            # Старый открытый чат после удаления/сброса пользователя должен
            # восстановить актуальную клубную привязку до создания Student.
            user_exists.club_id = club_id
            if not user_exists.full_name:
                user_exists.full_name = user_full_name
            await session.flush()

        # 2. Создаем атлета
        new_student = Student(
            parent_id=user_id,
            club_id=club_id,
            name=name,
            birthday=birthday_date,
            expire_date=None,
            balance_lessons=0,
            can_freeze=1,
            is_frozen=0,
            discipline=_default_student_discipline(club_settings)
        )
        session.add(new_student)
        await session.commit()  # Сохраняем в БД

        if birthday_date is None:
            logger.success(f"👤 Клиент сам добавил атлета: {name} без указания ДР (Клуб ID: {club_id})")
        else:
            logger.success(f"👤 Клиент сам добавил атлета: {name} с ДР {birthday_date} (Клуб ID: {club_id})")

        # ИСПРАВЛЕНО: Безопасно вытаскиваем имя клуба без триггера Lazy Load
        club_name = club.__dict__.get('name') or "нашем клубе"

        await message.answer(
            f"✅ Атлет <b>{name}</b> успешно зарегистрирован в <b>{club_name}</b>!\n\n"
            "Теперь вы можете купить абонемент или сформировать QR-пропуск в меню.",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(club_settings, club_id)
        )
        await state.clear()

    except Exception as e:
        await session.rollback()
        logger.error(f"❌ Ошибка при самостоятельном добавлении атлета: {e}")
        await message.answer("⚠️ Произошла ошибка при сохранении. Попробуйте позже.")


@router.callback_query(F.data == "auth_by_phone")
async def auth_by_phone_callback(callback: types.CallbackQuery, club: Club):
    builder = ReplyKeyboardBuilder()
    builder.row(types.KeyboardButton(text="📱 Поделиться контактом", request_contact=True))
    await callback.message.answer(
        f"📍 <b>Привязка профиля</b>\n\n"
        f"Клуб: <b>{club.name}</b>\n"
        f"Нажмите кнопку <b>«📱 Поделиться контактом»</b> внизу экрана, "
        f"чтобы система проверила ваш номер телефона в базе данных.",
        reply_markup=builder.as_markup(resize_keyboard=True, one_time_keyboard=True),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(F.contact)
async def process_user_contact(
        message: types.Message,
        session: AsyncSession,
        club: Club,
        club_settings: dict,
        redis: Redis
):
    # Очищаем номер от плюсов и пробелов
    raw_phone = message.contact.phone_number.replace("+", "").strip()
    clean_phone_10 = raw_phone[-10:]  # Последние 10 цифр (9991112233)
    user_id = message.from_user.id

    if not await rate_limit(redis, f"rl:bot:bind_phone:{club.id}:{user_id}", 3, 60):
        await audit_block("bot_bind_blocked", "rate_limited", club_id=club.id, user_id=user_id)
        return await message.answer(
            "Слишком часто. Подождите минуту и попробуйте снова.",
            reply_markup=types.ReplyKeyboardRemove()
        )

    try:
        # Ищем студентов, у которых телефон содержит последние 10 цифр
        stmt = select(Student).where(
            Student.parent_phone.contains(clean_phone_10),
            Student.club_id == club.id
        )
        result = await session.execute(stmt)
        students = result.scalars().all()

        if not students:
            return await message.answer(
                f"❌ Атлеты с номером <code>...{clean_phone_10[-4:]}</code> не найдены в базе клуба <b>{club.name}</b>.\n\n"
                "Свяжитесь с администратором, чтобы он внес ваш номер в систему.",
                parse_mode="HTML",
                reply_markup=types.ReplyKeyboardRemove()
            )

        # Безопасный импорт модели User, чтобы избежать падения, если пути отличаются
        try:
            from database.db import User
        except ImportError:
            from database.db import User  # Если модель лежит в другом месте, поправьте путь

        # Проверяем, зарегистрирован ли уже этот user_id в таблице users
        user_stmt = select(User).where(User.user_id == user_id)
        user_exists = (await session.execute(user_stmt)).scalar_one_or_none()

        if not user_exists:
            # Создаем запись для нового пользователя, чтобы не нарушать внешний ключ (ForeignKey)
            new_user = User(user_id=user_id)
            session.add(new_user)
            await session.flush()  # Синхронизируем с базой, чтобы ID зафиксировался

        # Привязываем Telegram ID ко всем найденным карточкам атлетов
        for student in students:
            student.parent_id = user_id
            session.add(student)

        await session.commit()
        names = ", ".join([f"<b>{s.name}</b>" for s in students])

        await message.answer(
            f"✅ Авторизация в <b>{club.name}</b> успешна!\n\n"
            f"Привязаны атлеты: {names}\n\n"
            "Теперь вам доступен личный кабинет и QR-пропуск.",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(club_settings, club.id)
        )

    except Exception as e:
        await session.rollback()
        logger.error(f"❌ Ошибка авторизации в клубе {club.id}: {e}")
        # Если была тишина, отправляем сообщение пользователю, чтобы он видел ошибку
        await message.answer("⚠️ Произошла внутренняя ошибка привязки профиля. Пожалуйста, сообщите администратору.")




@router.callback_query(F.data.startswith('schedule_'))
async def show_discipline_schedule(
        callback: types.CallbackQuery,
        club_settings: dict
):
    # 1. Извлекаем код (например: schedule_boxing -> boxing)
    discipline_code = callback.data.split('_')[1].lower()
    
    # 2. Лезем в конфиг за данными этой секции
    disciplines = club_settings.get("disciplines", {})
    discipline_cfg = disciplines.get(discipline_code)

    if not discipline_cfg:
        return await callback.answer("Упс! Данные этой секции не найдены 🛠", show_alert=True)

    name = discipline_cfg.get("name", discipline_code.upper())
    schedule_data = discipline_cfg.get("schedule")

    # 3. Парсим наше новое структурированное JSONB-расписание
    # Если там старая строка или вообще пусто — выводим красивую заглушку
    if not schedule_data or isinstance(schedule_data, str):
        final_schedule_text = "⏳ <i>Расписание временно не заполнено администратором.</i>"
    else:
        # Словарь для перевода ключей дней недели на человеческий русский
        day_translations = {
            "mon": "Понедельник", "tue": "Вторник", "wed": "Среда", 
            "thu": "Четверг", "fri": "Пятница", "sat": "Суббота", "sun": "Воскресенье"
        }
        
        lines = []
        # Проходим строго по порядку дней недели
        for day_key in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]:
            day_lessons = schedule_data.get(day_key, [])
            
            # Если в этот день есть тренировки — красиво их форматируем
            if day_lessons:
                lines.append(f"📌 <b>{day_translations[day_key]}:</b>")
                for lesson in day_lessons:
                    # Считаем свободные места (всего минус занято)
                    free_slots = max(0, lesson.get("max_slots", 15) - lesson.get("taken_slots", 0))
                    
                    lines.append(
                        f"  ⏱ <code>{lesson['time']}</code> — {lesson['coach']}\n"
                        f"  └ 👥 Мест осталось: <b>{free_slots}</b> из {lesson.get('max_slots', 15)}\n"
                    )
                lines.append("") # Делаем пустую строку-отступ между днями для читаемости
                
        # Собираем все строки вместе. Если массив пустой — значит админ завел структуру, но уроков 0
        if lines:
            final_schedule_text = "\n".join(lines)
        else:
            final_schedule_text = "⏳ <i>Расписание на эту неделю пока не заполнено.</i>"

    # 4. Выводим итоговый красивый результат пользователю (атлету или родителю)
    try:
        await callback.message.edit_text(
            text=(
                f"📅 <b>Расписание секции: {name}</b>\n\n"
                f"{final_schedule_text}\n"
                "<i>Используйте меню ниже для записи на занятия:</i>"
            ),
            reply_markup=get_section_menu_kb(discipline_code, name),
            parse_mode="HTML"
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise

    await callback.answer()
    
    
# @router.callback_query(F.data == "show_android_instructions")
# async def process_android_instruction(callback: types.CallbackQuery):
#     """
#     Раньше показывала инструкцию для Android-установки.
#     Сейчас не используется, потому что профиль открывается через WebApp напрямую.
#     """
#     await callback.answer()



