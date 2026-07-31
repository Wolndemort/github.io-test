from __future__ import annotations

import copy
import json
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timedelta

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from PIL import Image, UnidentifiedImageError
from loguru import logger
from redis.asyncio import Redis
from sqlalchemy import and_, or_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from database.db import Club, Student, PaymentOrder, CartOrder, VisitLog, User, get_daily_stats
from handlers.buttons import get_scanner_keyboard, admin_keyboard
from handlers.skud import save_and_test_turnstile
from handlers.states import AdminStates, AdminSettingsSG, TurnstileSetup
from services.analytics import reporting_periods
from services.audit import audit_event

router = Router()


@router.callback_query(F.data == "admin_settings")
async def admin_settings_menu(callback: types.CallbackQuery, club_settings: dict, club_id: int, is_owner: bool | None = None, is_super_admin: bool | None = None):
    if is_owner is False and is_super_admin is False:
        return await callback.answer("Доступ запрещен: эти настройки доступны только главному администратору.", show_alert=True)
    builder = InlineKeyboardBuilder()
    features = club_settings.get("features", {})
    limits = club_settings.get("limits", {})

    buttons = {
        "freeze": "Заморозка",
        "qr_checkin": "QR-чекины",
        "manual_add": "Ручные добавления",
        "online_payments": "Онлайн-оплаты",
    }

    for key, label in buttons.items():
        status = "✅" if features.get(key, False) else "❌"
        builder.row(types.InlineKeyboardButton(text=f"{status} {label}", callback_data=f"toggle_feat_{key}"))

    builder.row(types.InlineKeyboardButton(text="🛍 Настройки магазина / YooKassa", callback_data="admin_setup_yookassa"))
    ui = club_settings.get("ui", {})
    site_mark = "✅" if ui.get("site_enabled", False) else "❌"
    support_mark = "✅" if ui.get("support_enabled", True) else "❌"
    builder.row(types.InlineKeyboardButton(text=f"{site_mark} Сайт клуба", callback_data="toggle_site_link"), types.InlineKeyboardButton(text=f"{support_mark} Поддержка", callback_data="toggle_support_link"))
    builder.row(types.InlineKeyboardButton(text="✏️ Изменить сайт", callback_data="edit_site_url"), types.InlineKeyboardButton(text="✏️ Изменить username", callback_data="edit_support_username"))
    builder.row(types.InlineKeyboardButton(text="⚙️ Настройка лимитов клуба", callback_data="manage_club_limits"))
    loading_enabled = bool((ui.get("loading") or {}).get("enabled", False))
    loading_mark = "✅" if loading_enabled else "❌"
    builder.row(types.InlineKeyboardButton(text=f"{loading_mark} Загрузочный экран", callback_data="toggle_webapp_loading"))
    builder.row(types.InlineKeyboardButton(text="🖼 Загрузить логотип WebApp", callback_data="upload_webapp_logo"))
    builder.row(types.InlineKeyboardButton(text="💰 Настройка тарифов", callback_data="admin_tariffs_sections"))
    builder.row(types.InlineKeyboardButton(text="⏰ Планировщики", callback_data="admin_schedulers"))
    builder.row(types.InlineKeyboardButton(text="💳 Изменить реквизиты", callback_data="admin_edit_payments"))
    turnstile_config = club_settings.get("turnstile", {})
    t_status = "✅" if turnstile_config.get("enabled", False) else "❌"
    builder.row(types.InlineKeyboardButton(text=f"{t_status} СКУД(Турникет)", callback_data="admin_turnstile_main"))
    builder.row(types.InlineKeyboardButton(text="🥋 Управление секциями", callback_data="manage_disciplines"))
    builder.row(types.InlineKeyboardButton(text="⬅️ В админ-панель", callback_data="admin"))

    await callback.message.edit_text(
        "⚙️ <b>Настройки клуба</b>\n\nВыберите нужный раздел ниже:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )



@router.callback_query(F.data == "admin_schedulers")
async def admin_schedulers_menu(callback: types.CallbackQuery, club_settings: dict, club_id: int, is_owner: bool | None = None, is_super_admin: bool | None = None):
    if is_owner is False and is_super_admin is False:
        return await callback.answer("Доступ запрещён: настройки клуба доступны только главному администратору.", show_alert=True)
    builder = InlineKeyboardBuilder()
    features = club_settings.get("features", {})
    items = [
        ("birthday_missing_reminders", "Нет даты рождения"),
        ("subscription_expiry_reminders", "Окончание абонемента"),
        ("birthday_greetings", "Поздравления с ДР"),
        ("absence_reminders", "Прогульщики"),
        ("work_schedule_reminders", "График работы"),
        ("stock_reminders", "Склад"),
    ]
    for key, label in items:
        status = "✅" if features.get(key, True) else "❌"
        builder.row(types.InlineKeyboardButton(text=f"{status} {label}", callback_data=f"toggle_feat_{key}"))
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_settings"))
    await callback.message.edit_text(
        "⏰ <b>Планировщики клуба</b>\n\nВключайте и выключайте уведомления:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "manage_disciplines")
async def manage_disciplines_menu(callback: types.CallbackQuery, club_settings: dict):
    disciplines = club_settings.get("disciplines", {})
    builder = InlineKeyboardBuilder()

    if not disciplines:
        builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_settings"))
        return await callback.message.edit_text(
            "🥋 <b>Список направлений пуст</b>\n\nОбратитесь к супер-админу для настройки базы секций.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )

    for code, info in disciplines.items():
        status = "✅" if info.get("active") else "❌"
        name = info.get("name", code.upper())
        builder.row(types.InlineKeyboardButton(text=f"{status} {name}", callback_data=f"toggle_disc_{code}"))

    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_settings"))
    await callback.message.edit_text(
        "🥋 <b>Список направлений</b>\n\nОтметьте секции, которые работают в вашем клубе:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("toggle_feat_") | F.data.startswith("toggle_disc_"))
async def toggle_logic(callback: types.CallbackQuery, club: Club, club_settings: dict, session: AsyncSession, redis: Redis):
    parts = callback.data.split("_")
    action_type = parts[1]
    target_key = "_".join(parts[2:])
    scheduler_keys = {
        "birthday_missing_reminders",
        "subscription_expiry_reminders",
        "birthday_greetings",
        "absence_reminders",
        "work_schedule_reminders",
        "stock_reminders",
    }

    if action_type == "feat":
        features = club_settings.setdefault("features", {})
        current = features.get(target_key, True)
        features[target_key] = not current
    elif action_type == "disc":
        disciplines = club_settings.setdefault("disciplines", {})
        disc_info = disciplines.get(target_key)
        if disc_info:
            disc_info["active"] = not disc_info.get("active", True)
        else:
            return await callback.answer(f"Ошибка: {target_key} не найден", show_alert=True)

    await session.execute(update(Club).where(Club.id == club.id).values(club_settings=club_settings))
    await session.commit()
    await redis.delete(f"club_config:{callback.bot.token}")

    audit_event(
        "club_setting_toggled",
        club_id=club.id,
        action="toggle",
        object_type="club_setting",
        object_id=target_key,
        location="bot/admin_settings",
    )

    await callback.answer("✅ Сохранено")
    if action_type == "feat":
        if target_key in scheduler_keys:
            await admin_schedulers_menu(callback, club_settings, club.id)
        else:
            await admin_settings_menu(callback, club_settings, club.id)
    else:
        await manage_disciplines_menu(callback, club_settings)


@router.callback_query(F.data == "admin_public_links")
async def admin_public_links_start(callback: types.CallbackQuery, state: FSMContext, club_settings: dict, is_owner: bool, is_super_admin: bool):
    if not (is_owner or is_super_admin):
        return await callback.answer("Доступ запрещён", show_alert=True)
    ui = club_settings.get("ui", {})
    await state.set_state(AdminSettingsSG.waiting_for_public_links)
    await callback.message.answer(
        "Введите одной строкой через |:\nsite_url | support_username | site_on (1/0) | support_on (1/0)\n\n"
        f"Текущие: {ui.get('site_url','')} | {ui.get('support_link','')} | {int(ui.get('site_enabled', False))} | {int(ui.get('support_enabled', True))}"
    )
    await callback.answer()


@router.callback_query(F.data.in_({"toggle_site_link", "toggle_support_link"}))
async def toggle_public_link(callback: types.CallbackQuery, club: Club, club_settings: dict, session: AsyncSession, redis: Redis, is_owner: bool, is_super_admin: bool):
    if not (is_owner or is_super_admin):
        return await callback.answer("Доступ запрещён", show_alert=True)
    key = "site_enabled" if callback.data == "toggle_site_link" else "support_enabled"
    ui = club_settings.setdefault("ui", {})
    ui[key] = not ui.get(key, key == "support_enabled")
    await session.execute(update(Club).where(Club.id == club.id).values(club_settings=club_settings))
    await session.commit()
    await redis.delete(f"club_config:{callback.bot.token}")
    await callback.answer("✅ Переключено")
    await admin_settings_menu(callback, club_settings, club.id)


@router.callback_query(F.data == "edit_site_url")
async def edit_site_url(callback: types.CallbackQuery, state: FSMContext, is_owner: bool, is_super_admin: bool):
    if not (is_owner or is_super_admin):
        return await callback.answer("Доступ запрещён", show_alert=True)
    await state.set_state(AdminSettingsSG.waiting_for_site_url)
    await callback.message.answer("Отправьте URL сайта (https://...):")
    await callback.answer()


@router.callback_query(F.data == "edit_support_username")
async def edit_support_username(callback: types.CallbackQuery, state: FSMContext, is_owner: bool, is_super_admin: bool):
    if not (is_owner or is_super_admin):
        return await callback.answer("Доступ запрещён", show_alert=True)
    await state.set_state(AdminSettingsSG.waiting_for_support_username)
    await callback.message.answer("Отправьте Telegram username поддержки, например @admin:")
    await callback.answer()


@router.message(AdminSettingsSG.waiting_for_site_url)
async def save_site_url(message: types.Message, state: FSMContext, club: Club, club_settings: dict, session: AsyncSession, redis: Redis, is_owner: bool, is_super_admin: bool):
    if not (is_owner or is_super_admin):
        return await message.answer("Доступ запрещён")
    value = (message.text or "").strip()
    if not value.startswith(("https://", "http://")):
        return await message.answer("URL должен начинаться с https:// или http://")
    club_settings.setdefault("ui", {})["site_url"] = value
    await session.execute(update(Club).where(Club.id == club.id).values(club_settings=club_settings))
    await session.commit()
    await redis.delete(f"club_config:{message.bot.token}")
    await state.clear()
    await message.answer("✅ Сайт сохранён")


@router.message(AdminSettingsSG.waiting_for_support_username)
async def save_support_username(message: types.Message, state: FSMContext, club: Club, club_settings: dict, session: AsyncSession, redis: Redis, is_owner: bool, is_super_admin: bool):
    if not (is_owner or is_super_admin):
        return await message.answer("Доступ запрещён")
    value = (message.text or "").strip().lstrip("@")
    if not value or any(ch.isspace() for ch in value):
        return await message.answer("Введите корректный username Telegram")
    club_settings.setdefault("ui", {})["support_link"] = value
    await session.execute(update(Club).where(Club.id == club.id).values(club_settings=club_settings))
    await session.commit()
    await redis.delete(f"club_config:{message.bot.token}")
    await state.clear()
    await message.answer("✅ Username поддержки сохранён")


@router.message(AdminSettingsSG.waiting_for_public_links)
async def admin_public_links_save(message: types.Message, state: FSMContext, club: Club, club_settings: dict, session: AsyncSession, redis: Redis):
    parts = [x.strip() for x in (message.text or "").split("|")]
    if len(parts) != 4 or parts[2] not in {"0", "1"} or parts[3] not in {"0", "1"}:
        return await message.answer("Неверный формат. Нужно: сайт | @поддержка | 1/0 | 1/0")
    site, support = parts[0], parts[1].lstrip("@")
    if site and not site.startswith(("https://", "http://")):
        return await message.answer("Сайт должен начинаться с https:// или http://")
    club_settings.setdefault("ui", {}).update({"site_url": site, "support_link": support, "site_enabled": parts[2] == "1", "support_enabled": parts[3] == "1"})
    await session.execute(update(Club).where(Club.id == club.id).values(club_settings=club_settings))
    await session.commit()
    await redis.delete(f"club_config:{message.bot.token}")
    await state.clear()
    await message.answer("✅ Сайт и поддержка сохранены")


@router.callback_query(F.data == "daily_report")
async def show_daily_report(
    callback: types.CallbackQuery,
    club,
    club_settings: dict,
    is_owner: bool,
    is_super_admin: bool,
    session: AsyncSession,
    redis,
):
    if not (is_owner or is_super_admin):
        return await callback.answer("❌ Доступ ограничен.", show_alert=True)
    lock_key = f"lock:report:{club.id}"
    if await redis.get(lock_key):
        return await callback.answer("⏳ Секунду, данные загружаются...", show_alert=False)
    await redis.set(lock_key, "1", ex=5)

    try:
        periods = reporting_periods()
        now = periods["now"]
        start_of_today = periods["today"]
        start_of_yesterday = periods["yesterday"]
        sleeping_threshold = now - timedelta(days=14)
        visits, active_passes = await get_daily_stats(club_id=club.id, session=session)
        today_rev_res = await session.execute(select(func.coalesce(func.sum(PaymentOrder.amount_kopecks), 0)).where(and_(PaymentOrder.club_id == club.id, PaymentOrder.status == "CONFIRMED", PaymentOrder.created_at >= start_of_today)))
        today_cart_res = await session.execute(select(func.coalesce(func.sum(CartOrder.amount_kopecks), 0)).where(CartOrder.club_id == club.id, CartOrder.status == "CONFIRMED", CartOrder.created_at >= start_of_today))
        revenue_today = (today_rev_res.scalar_one() + today_cart_res.scalar_one()) / 100
        yesterday_rev_res = await session.execute(select(func.coalesce(func.sum(PaymentOrder.amount_kopecks), 0)).where(and_(PaymentOrder.club_id == club.id, PaymentOrder.status == "CONFIRMED", PaymentOrder.created_at >= start_of_yesterday, PaymentOrder.created_at < start_of_today)))
        yesterday_cart_res = await session.execute(select(func.coalesce(func.sum(CartOrder.amount_kopecks), 0)).where(CartOrder.club_id == club.id, CartOrder.status == "CONFIRMED", CartOrder.created_at >= start_of_yesterday, CartOrder.created_at < start_of_today))
        revenue_yesterday = (yesterday_rev_res.scalar_one() + yesterday_cart_res.scalar_one()) / 100
        stats_res = await session.execute(
            select(
                func.count(Student.id).label("total"),
                func.count(Student.id).filter(and_(
                    func.coalesce(Student.is_frozen, 0) == 0,
                    or_(Student.balance_lessons <= 0, Student.expire_date.is_(None), Student.expire_date <= now),
                )).label("expired"),
                func.count(Student.id).filter(or_(Student.last_visit.is_(None), Student.last_visit <= sleeping_threshold)).label("sleeping"),
                func.count(func.distinct(Student.parent_id)).filter(Student.parent_id.is_not(None)).label("parents"),
            ).where(Student.club_id == club.id)
        )
        stats = stats_res.one()
        disc_visits_res = await session.execute(
            select(Student.discipline, func.count(VisitLog.id))
            .join(Student, VisitLog.student_id == Student.id)
            .where(VisitLog.club_id == club.id, VisitLog.visited_at >= start_of_today, VisitLog.visited_at < start_of_today + timedelta(days=1))
            .group_by(Student.discipline)
        )
        disc_visits_rows = disc_visits_res.all()
        config_disciplines = club_settings.get("disciplines", {})
        visits_by_discipline_text = ""
        if disc_visits_rows:
            for disc_key, count in disc_visits_rows:
                human_name = config_disciplines.get(disc_key.lower(), {}).get("name", disc_key or "Без секции")
                visits_by_discipline_text += f"🥋 {human_name}: <code>{count}</code>\n"
        top_discipline_name = "Нет данных"
        top_discipline = "Нет данных"
        if disc_visits_rows:
            top_disc_key, top_visits = max(disc_visits_rows, key=lambda item: item[1])
            top_discipline = top_visits
            top_discipline_name = config_disciplines.get((top_disc_key or "").lower(), {}).get("name", top_disc_key or "Без секции")
        report_text = (
            f"📊 <b>ДНЕВНОЙ ОТЧЁТ: {club.name}</b>\n"
            f"📅 Дата: <code>{now.strftime('%d.%m.%Y')}</code>\n\n"
            f"💰 Касса сегодня: <code>{revenue_today:.0f} ₽</code>\n"
            f"⚖️ Динамика ко вчера: <code>{revenue_today - revenue_yesterday:+.0f} ₽</code>\n"
            f"🥋 Всего атлетов: <code>{stats.total}</code>\n"
            f"👥 Родителей с привязкой: <code>{stats.parents}</code>\n"
            f"🚶‍♂️ Посещений: <code>{visits}</code>\n"
            f"💎 Действующих абонементов: <code>{active_passes}</code>\n"
            f"❌ Закончился баланс: <code>{stats.expired}</code>\n"
            f"last_visit 💤 Спящие (>14 дней): <code>{stats.sleeping}</code>\n"
            f"🥋 Главное направление: <code>{top_discipline_name}</code>\n\n"
            f"{visits_by_discipline_text}"
        )
        await callback.message.answer(report_text, parse_mode="HTML")
        await callback.answer()
    finally:
        await redis.delete(lock_key)


@router.callback_query(F.data == "admin_turnstile_main")
async def admin_turnstile_main(callback: types.CallbackQuery, club_settings: dict, club: Club, is_owner: bool | None = None, is_super_admin: bool | None = None):
    if not (is_owner or is_super_admin):
        return await callback.answer("Доступ запрещён", show_alert=True)
    turnstile_config = club_settings.get("turnstile", {})
    is_enabled = turnstile_config.get("enabled", False)
    builder = InlineKeyboardBuilder()

    if not is_enabled:
        builder.row(types.InlineKeyboardButton(text="🪛 Настроить и включить", callback_data="setup_t_start"))
        builder.row(types.InlineKeyboardButton(text="🛠 Назад в настройки", callback_data="admin_settings"))
        await callback.message.edit_text(
            "📡 <b>Интеграция СКУД (Турникет)</b>\n\n"
            "Функция отключена.\n"
            "Для подключения вам понадобится реле DTWONDER (dingtian) и настроенный KeenDNS адрес.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
    else:
        builder.row(types.InlineKeyboardButton(text="🔄 зменить настройки", callback_data="setup_t_start"))
        builder.row(types.InlineKeyboardButton(text="🛑 Выключить СКУД", callback_data="disable_t_confirm"))
        builder.row(types.InlineKeyboardButton(text="🛠 Назад в настройки", callback_data="admin_settings"))
        current_url = turnstile_config.get("base_url", "Не задан")
        await callback.message.edit_text(
            f"📡 <b>Интеграция СКУД (Турникет) активна</b>\n\n"
            f"📌 Текущий адрес реле: <code>{current_url}</code>\n\n"
            f"Вы можете изменить параметры или отключить интеграцию.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
    audit_event(
        "turnstile_panel_opened",
        club_id=None,
        action="view",
        object_type="turnstile_config",
        object_id="turnstile",
        location="bot/admin_turnstile",
        actor_user_id=callback.from_user.id,
        actor_role="super_admin" if is_super_admin else "owner",
        actor_name=callback.from_user.full_name,
        enabled=is_enabled,
    )


@router.callback_query(F.data == "setup_t_start")
async def setup_turnstile_url_step(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(TurnstileSetup.wait_for_url)
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🛠 Назад в настройки", callback_data="admin_settings"))
    await callback.message.edit_text(
        "📝 <b>Шаг 1: Введите адрес KeenDNS (или IP)</b>\n\n"
        "⚠️ Протокол (http://) и порты указывать не нужно, бот подставит их сам.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    audit_event(
        "turnstile_setup_started",
        actor_user_id=callback.from_user.id,
        actor_role="owner",
        actor_name=callback.from_user.full_name,
        action="create",
        object_type="turnstile_config",
        object_id="turnstile",
        location="bot/admin_turnstile",
    )


@router.message(TurnstileSetup.wait_for_url)
async def process_t_url(message: types.Message, state: FSMContext):
    url_input = message.text.strip().lower()
    if not (url_input.startswith("http://") or url_input.startswith("https://")):
        url_input = f"http://{url_input}"
    await state.update_data(base_url=url_input)
    await state.set_state(TurnstileSetup.wait_for_password)
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="❌ Без пароля (Пропустить)", callback_data="skip_t_password"))
    await message.answer(
        "🔐 <b>Шаг 2: Введите пароль от веб-панели реле</b>\n\n"
        "Если вы установили пароль на доступ к плате, то введите его сейчас в ответном сообщении.\n"
        "Если на плате остался стандартный доступ без пароля, нажмите на кнопку ниже:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(TurnstileSetup.wait_for_password, F.data == "skip_t_password")
async def skip_t_password(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, club: Club):
    user_data = await state.get_data()
    await state.clear()
    await callback.answer()
    await save_and_test_turnstile(callback.message, session, club, user_data["base_url"], password="")


@router.message(TurnstileSetup.wait_for_password)
async def process_t_password(message: types.Message, state: FSMContext, session: AsyncSession, club: Club):
    password_input = message.text.strip()
    user_data = await state.get_data()
    await state.clear()
    await save_and_test_turnstile(message, session, club, user_data["base_url"], password_input)


@router.callback_query(F.data == "disable_t_confirm")
async def disable_turnstile(callback: types.CallbackQuery, session: AsyncSession, club: Club):
    current_settings = dict(club.settings) if club.settings else {}
    if "turnstile" in current_settings:
        current_settings["turnstile"]["enabled"] = False
        club.settings = current_settings
        try:
            db_club = await session.merge(club)
            flag_modified(db_club, "settings")
            await session.commit()
            await callback.answer("🔒 Интеграция СКУД успешно отключена", show_alert=True)
        except Exception as e:
            audit_event(
                "turnstile_disable_failed",
                club_id=club.id,
                action="error",
                object_type="turnstile_config",
                object_id="turnstile",
                location="bot/admin_turnstile",
                error=str(e),
            )
            await session.rollback()
            await callback.answer("❌ Не удалось сохранить изменения в БД", show_alert=True)
            return
        await admin_turnstile_main(callback, club_settings=current_settings, club=club)
    else:
        await callback.answer("СКУД и так не был настроен", show_alert=True)


@router.callback_query(F.data == "manage_club_limits")
async def manage_club_limits_handler(callback: types.CallbackQuery, club: Club):
    club_settings = club.club_settings or {}
    limits = club_settings.get("limits", {})
    timeout = limits.get("session_timeout_minutes", 150)
    freeze_step = limits.get("freeze_days_step", 7)
    freeze_price = limits.get("freeze_price_per_day", 0)
    text = (
        f"⚙️ <b>Управление лимитами клуба «{club.name}»</b>\n\n"
        f"⏱ <b>Сессия визита (СКУД):</b> <code>{timeout} мин.</code> ({timeout / 60:.1f} ч.)\n"
        f"<i>В течение этого времени повторные проходы через турникет не списывают занятия.</i>\n\n"
        f"❄️ <b>Шаг заморозки абонемента:</b> <code>{freeze_step} дн.</code>\n"
        f"<i>Минимальный пакет дней, на который списывается заморозка.</i>\n\n"
        f"💳 <b>Платная заморозка:</b> <code>{freeze_price} ₽/день</code>\n"
        f"<i>0 — покупка заморозки отключена.</i>\n\n"
        f"Выберите параметр для изменения:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱ зменить время сессии", callback_data="change_limit_session")],
        [InlineKeyboardButton(text="❄️ зменить шаг заморозки", callback_data="change_limit_freeze")],
        [InlineKeyboardButton(text="💳 Цена 1 дня заморозки", callback_data="change_freeze_price")],
        [InlineKeyboardButton(text="⬅️ Назад в настройки", callback_data="admin_settings")]
    ])
    await callback.message.edit_text(text=text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "change_limit_session")
async def change_limit_session(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminSettingsSG.waiting_for_session_timeout)
    await callback.message.answer("⏱ <b>Введите новое время сессии визита в минутах</b> (например, 120 для 2 часов):", parse_mode="HTML")
    await callback.answer()


@router.message(AdminSettingsSG.waiting_for_session_timeout)
async def process_session_timeout(message: types.Message, state: FSMContext, session: AsyncSession, club: Club, redis: Redis):
    if not message.text.isdigit():
        return await message.answer("❌ Ошибка: Введите целое число минут!")
    minutes = int(message.text)
    if minutes < 1 or minutes > 1440:
        return await message.answer("❌ Ошибка: Время сессии должно быть в диапазоне от 1 до 1440 минут (24 часа)!")
    if not club.club_settings or not isinstance(club.club_settings, dict):
        club.club_settings = {}
    if "limits" not in club.club_settings or not isinstance(club.club_settings["limits"], dict):
        club.club_settings["limits"] = {}
    club.club_settings["limits"]["session_timeout_minutes"] = minutes
    try:
        db_club = await session.merge(club)
        flag_modified(db_club, "club_settings")
        await session.commit()
        await redis.delete(f"club_config:{message.bot.token}")
    except Exception as e:
        logger.error(f"Ошибка при сохранении таймаута СКУД: {e}")
        await session.rollback()
        return await message.answer("❌ Не удалось сохранить изменения лимитов в БД.")
    await state.clear()
    await message.answer(f"✅ <b>Время СКУД-сессии успешно изменено на {minutes} минут!</b>", parse_mode="HTML")


@router.callback_query(F.data == "change_limit_freeze")
async def change_limit_freeze(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminSettingsSG.waiting_for_freeze_step)
    await callback.message.answer("❄️ <b>Введите новый минимальный шаг заморозки в днях</b> (например, 7):", parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "configure_webapp_loading")
async def configure_webapp_loading(callback: types.CallbackQuery, state: FSMContext, club_settings: dict):
    ui = club_settings.get("ui", {}) or {}
    loading = ui.get("loading", {}) or {}
    await state.set_state(AdminSettingsSG.waiting_for_loading_config)
    await callback.message.answer(
        "🎨 Отправьте JSON одной строкой:\n"
        '<code>{"enabled":true,"logo_url":"https://.../logo.png","duration_ms":1200,"message":"Загружаем приложение…"}</code>\n\n'
        f"Сейчас: {'включён' if loading.get('enabled') else 'выключен'}.", parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "toggle_webapp_loading")
async def toggle_webapp_loading(callback: types.CallbackQuery, club: Club, club_settings: dict, session: AsyncSession, redis: Redis):
    settings = dict(club_settings or {})
    ui = dict(settings.get("ui") or {})
    loading = dict(ui.get("loading") or {})
    loading["enabled"] = not bool(loading.get("enabled", False))
    ui["loading"] = loading
    settings["ui"] = ui
    db_club = await session.merge(club)
    db_club.club_settings = settings
    flag_modified(db_club, "club_settings")
    await session.commit()
    await redis.delete(f"club_config:{callback.bot.token}")
    await callback.answer("Загрузочный экран включён" if loading["enabled"] else "Загрузочный экран выключен")
    await admin_settings_menu(callback, settings, club.id)


@router.callback_query(F.data == "upload_webapp_logo")
async def upload_webapp_logo(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminSettingsSG.waiting_for_loading_logo)
    await callback.message.answer("🖼 Отправьте изображение логотипа одним сообщением.")
    await callback.answer()


@router.message(AdminSettingsSG.waiting_for_loading_logo, F.photo)
async def save_webapp_logo(message: types.Message, state: FSMContext, club: Club, session: AsyncSession, redis: Redis):
    if not message.photo or (message.photo[-1].file_size and message.photo[-1].file_size > 8 * 1024 * 1024):
        return await message.answer("❌ Логотип должен быть изображением не больше 8 МБ.")
    folder = Path("static/uploads/logos")
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"club_{club.id}_{uuid4().hex}.jpg"
    path = folder / filename
    try:
        await message.bot.download(message.photo[-1], destination=path)
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((2000, 2000), Image.Resampling.LANCZOS)
            image.save(path, format="JPEG", quality=88, optimize=True)
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("logo is too large after conversion")
        settings = dict(club.club_settings or {})
        ui = dict(settings.get("ui") or {})
        logo_url = f"/static/uploads/logos/{filename}"
        ui["logo_url"] = logo_url
        loading = dict(ui.get("loading") or {})
        loading["enabled"] = True
        loading["logo_url"] = logo_url
        loading["logo_rev"] = uuid4().hex
        loading.setdefault("duration_ms", 1200)
        loading.setdefault("message", "Загружаем приложение…")
        ui["loading"] = loading
        settings["ui"] = ui
        db_club = await session.merge(club)
        db_club.club_settings = settings
        flag_modified(db_club, "club_settings")
        await session.commit()
    except Exception:
        await session.rollback()
        path.unlink(missing_ok=True)
        logger.exception("Ошибка загрузки логотипа WebApp")
        return await message.answer("❌ Не удалось сохранить логотип. Попробуйте ещё раз.")
    await redis.delete(f"club_config:{message.bot.token}")
    await state.clear()
    await message.answer("✅ Логотип загружен и сохранён.")


@router.message(AdminSettingsSG.waiting_for_loading_logo)
async def invalid_webapp_logo(message: types.Message):
    await message.answer("Отправьте именно изображение логотипа (фото).")


@router.message(AdminSettingsSG.waiting_for_loading_config)
async def save_webapp_loading(message: types.Message, state: FSMContext, club: Club, session: AsyncSession, redis: Redis):
    try:
        value = json.loads(message.text or "")
        if not isinstance(value, dict):
            raise ValueError
        enabled = bool(value.get("enabled", False))
        logo_url = str(value.get("logo_url", "")).strip()
        duration = int(value.get("duration_ms", 1200))
        text = str(value.get("message", "Загружаем приложение…")).strip()[:120]
        if duration < 300 or duration > 10000:
            return await message.answer("duration_ms должен быть от 300 до 10000.")
    except (ValueError, TypeError, json.JSONDecodeError):
        return await message.answer("Неверный JSON. Проверьте пример и отправьте ещё раз.")
    settings = dict(club.club_settings or {})
    ui = dict(settings.get("ui") or {})
    if logo_url:
        ui["logo_url"] = logo_url
        loading_rev = uuid4().hex
    else:
        logo_url = str(ui.get("logo_url", "")).strip()
        loading_rev = str((ui.get("loading") or {}).get("logo_rev", "")).strip() or uuid4().hex
    ui["logo_url"] = logo_url
    ui["loading"] = {"enabled": enabled, "duration_ms": duration, "message": text}
    if logo_url:
        ui["loading"]["logo_url"] = logo_url
    ui["loading"]["logo_rev"] = loading_rev
    settings["ui"] = ui
    db_club = await session.merge(club)
    db_club.club_settings = settings
    flag_modified(db_club, "club_settings")
    await session.commit()
    await redis.delete(f"club_config:{message.bot.token}")
    await state.clear()
    await message.answer("✅ Настройки загрузочного экрана WebApp сохранены.")


@router.callback_query(F.data == "change_freeze_price")
async def change_freeze_price(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminSettingsSG.waiting_for_freeze_price)
    await callback.message.answer("💳 Введите цену одного дня заморозки в рублях (0 — отключить):")
    await callback.answer()


@router.message(AdminSettingsSG.waiting_for_freeze_price)
async def process_freeze_price(message: types.Message, state: FSMContext, club: Club, session: AsyncSession, redis: Redis):
    try:
        price = float((message.text or "").replace(",", ".").strip())
    except ValueError:
        return await message.answer("Введите число, например 150 или 0.")
    if price < 0 or price > 100000:
        return await message.answer("Цена должна быть от 0 до 100000 ₽.")
    settings = dict(club.club_settings or {})
    limits = dict(settings.get("limits") or {})
    limits["freeze_price_per_day"] = price
    settings["limits"] = limits
    db_club = await session.merge(club)
    db_club.club_settings = settings
    flag_modified(db_club, "club_settings")
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("Ошибка сохранения цены платной заморозки")
        return await message.answer("❌ Не удалось сохранить цену. Попробуйте ещё раз.")
    await redis.delete(f"club_config:{message.bot.token}")
    await state.clear()
    await message.answer(f"✅ Цена платной заморозки: {price:g} ₽ за день.")


@router.message(AdminSettingsSG.waiting_for_freeze_step)
async def process_freeze_step(message: types.Message, state: FSMContext, session: AsyncSession, club: Club, redis: Redis):
    if not message.text.isdigit():
        return await message.answer("❌ Ошибка: Введите целое число дней!")
    days = int(message.text)
    if days < 1 or days > 30:
        return await message.answer("❌ Ошибка: Шаг заморозки должен быть от 1 до 30 дней!")
    if not club.club_settings or not isinstance(club.club_settings, dict):
        club.club_settings = {}
    if "limits" not in club.club_settings or not isinstance(club.club_settings["limits"], dict):
        club.club_settings["limits"] = {}
    club.club_settings["limits"]["freeze_days_step"] = days
    try:
        db_club = await session.merge(club)
        flag_modified(db_club, "club_settings")
        await session.commit()
        await redis.delete(f"club_config:{message.bot.token}")
    except Exception as e:
        logger.error(f"Ошибка при сохранении шага заморозки: {e}")
        await session.rollback()
        return await message.answer("❌ Не удалось сохранить изменения шага заморозки в БД.")
    await state.clear()
    await message.answer(f"✅ <b>Минимальный шаг заморозки успешно изменен на {days} дней!</b>", parse_mode="HTML")


