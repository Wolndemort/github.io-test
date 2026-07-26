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
        return await callback.answer("Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰С‘РЅ: РЅР°СЃС‚СЂРѕР№РєРё РєР»СѓР±Р° РґРѕСЃС‚СѓРїРЅС‹ С‚РѕР»СЊРєРѕ РіР»Р°РІРЅРѕРјСѓ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂСѓ.", show_alert=True)
    builder = InlineKeyboardBuilder()
    features = club_settings.get("features", {})
    limits = club_settings.get("limits", {})

    buttons = {
        "freeze": "Р—Р°РјРѕСЂРѕР·РєР°",
        "qr_checkin": "QR-РІС…РѕРґ",
        "manual_add": "Р СѓС‡РЅРѕРµ РґРѕР±Р°РІР»РµРЅРёРµ",
        "online_payments": "РћРЅР»Р°Р№РЅ-РїР»Р°С‚РµР¶Рё",
        "stock_reminders": "РќР°РїРѕРјРёРЅР°РЅРёСЏ РїРѕ СЃРєР»Р°РґСѓ",
    }

    for key, label in buttons.items():
        status = "вњ…" if features.get(key, False) else "вќЊ"
        builder.row(types.InlineKeyboardButton(text=f"{status} {label}", callback_data=f"toggle_feat_{key}"))

    builder.row(types.InlineKeyboardButton(text="рџ›Ќ РќР°СЃС‚СЂРѕР№РєРё РјР°РіР°Р·РёРЅР° / YooKassa", callback_data="admin_setup_yookassa"))
    # РЈРїСЂР°РІР»РµРЅРёРµ С‚РѕРІР°СЂР°РјРё РІС‹РЅРµСЃРµРЅРѕ РІ Р°РґРјРёРЅСЃРєРёР№ WebApp/РїР°РЅРµР»СЊ, РґСѓР±Р»Рё РІ РЅР°СЃС‚СЂРѕР№РєР°С… СѓР±РёСЂР°РµРј.
    builder.row(types.InlineKeyboardButton(text="рџ•’ Р“СЂР°С„РёРє СЂР°Р±РѕС‚С‹", web_app=types.WebAppInfo(url=f"https://{club_id}.speedycrm.ru/webapp/admin-work-schedule?club_id={club_id}")))
    ui = club_settings.get("ui", {})
    site_mark = "вњ…" if ui.get("site_enabled", False) else "вќЊ"
    support_mark = "вњ…" if ui.get("support_enabled", True) else "вќЊ"
    builder.row(types.InlineKeyboardButton(text=f"{site_mark} РЎР°Р№С‚ РєР»СѓР±Р°", callback_data="toggle_site_link"), types.InlineKeyboardButton(text=f"{support_mark} РџРѕРґРґРµСЂР¶РєР°", callback_data="toggle_support_link"))
    builder.row(types.InlineKeyboardButton(text="вњЏпёЏ РР·РјРµРЅРёС‚СЊ СЃР°Р№С‚", callback_data="edit_site_url"), types.InlineKeyboardButton(text="вњЏпёЏ РР·РјРµРЅРёС‚СЊ username", callback_data="edit_support_username"))
    builder.row(types.InlineKeyboardButton(text="вљ™пёЏ РќР°СЃС‚СЂРѕР№РєР° Р»РёРјРёС‚РѕРІ РєР»СѓР±Р°", callback_data="manage_club_limits"))
    loading_enabled = bool((ui.get("loading") or {}).get("enabled", False))
    loading_mark = "вњ…" if loading_enabled else "вќЊ"
    builder.row(types.InlineKeyboardButton(text=f"{loading_mark} Р—Р°РіСЂСѓР·РѕС‡РЅС‹Р№ СЌРєСЂР°РЅ", callback_data="toggle_webapp_loading"), types.InlineKeyboardButton(text="вљ™пёЏ РќР°СЃС‚СЂРѕРёС‚СЊ", callback_data="configure_webapp_loading"))
    builder.row(types.InlineKeyboardButton(text="рџ–ј Р—Р°РіСЂСѓР·РёС‚СЊ Р»РѕРіРѕС‚РёРї WebApp", callback_data="upload_webapp_logo"))
    builder.row(types.InlineKeyboardButton(text="рџ’° РќР°СЃС‚СЂРѕР№РєР° С‚Р°СЂРёС„РѕРІ", callback_data="admin_tariffs_sections"))
    builder.row(types.InlineKeyboardButton(text="рџ’° РўР°СЂРёС„С‹ (WebApp)", web_app=types.WebAppInfo(url=f"https://{club_id}.speedycrm.ru/webapp/admin-tariffs?club_id={club_id}")))
    builder.row(types.InlineKeyboardButton(text="⏰ Планировщики", callback_data="admin_schedulers"))
    builder.row(types.InlineKeyboardButton(text="рџ’і РР·РјРµРЅРёС‚СЊ СЂРµРєРІРёР·РёС‚С‹", callback_data="admin_edit_payments"))
    turnstile_config = club_settings.get("turnstile", {})
    t_status = "вњ…" if turnstile_config.get("enabled", False) else "вќЊ"
    builder.row(types.InlineKeyboardButton(text=f"{t_status} РЎРљРЈР”(РўСѓСЂРЅРёРєРµС‚)", callback_data="admin_turnstile_main"))
    builder.row(types.InlineKeyboardButton(text="рџҐ‹ РЈРїСЂР°РІР»РµРЅРёРµ СЃРµРєС†РёСЏРјРё", callback_data="manage_disciplines"))
    builder.row(types.InlineKeyboardButton(text="в¬…пёЏ Р’ Р°РґРјРёРЅ-РїР°РЅРµР»СЊ", callback_data="admin"))

    await callback.message.edit_text(
        "рџ›  <b>РќР°СЃС‚СЂРѕР№РєРё РјРѕРґСѓР»РµР№ РєР»СѓР±Р°</b>\n\nР’РєР»СЋС‡Р°Р№С‚Рµ Рё РІС‹РєР»СЋС‡Р°Р№С‚Рµ С„СѓРЅРєС†РёРё Р±РѕС‚Р°:",
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
        builder.row(types.InlineKeyboardButton(text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data="admin_settings"))
        return await callback.message.edit_text(
            "рџҐ‹ <b>РЎРїРёСЃРѕРє РЅР°РїСЂР°РІР»РµРЅРёР№ РїСѓСЃС‚</b>\n\nРћР±СЂР°С‚РёС‚РµСЃСЊ Рє СЃСѓРїРµСЂ-Р°РґРјРёРЅСѓ РґР»СЏ РЅР°СЃС‚СЂРѕР№РєРё Р±Р°Р·С‹ СЃРµРєС†РёР№.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )

    for code, info in disciplines.items():
        status = "вњ…" if info.get("active") else "вќЊ"
        name = info.get("name", code.upper())
        builder.row(types.InlineKeyboardButton(text=f"{status} {name}", callback_data=f"toggle_disc_{code}"))

    builder.row(types.InlineKeyboardButton(text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data="admin_settings"))
    await callback.message.edit_text(
        "рџҐ‹ <b>РЎРїРёСЃРѕРє РЅР°РїСЂР°РІР»РµРЅРёР№</b>\n\nРћС‚РјРµС‚СЊС‚Рµ СЃРµРєС†РёРё, РєРѕС‚РѕСЂС‹Рµ СЂР°Р±РѕС‚Р°СЋС‚ РІ РІР°С€РµРј РєР»СѓР±Рµ:",
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
            return await callback.answer(f"РћС€РёР±РєР°: {target_key} РЅРµ РЅР°Р№РґРµРЅ", show_alert=True)

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

    await callback.answer("вњ… РЎРѕС…СЂР°РЅРµРЅРѕ")
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
        return await callback.answer("Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰С‘РЅ", show_alert=True)
    ui = club_settings.get("ui", {})
    await state.set_state(AdminSettingsSG.waiting_for_public_links)
    await callback.message.answer(
        "Р’РІРµРґРёС‚Рµ РѕРґРЅРѕР№ СЃС‚СЂРѕРєРѕР№ С‡РµСЂРµР· |:\nsite_url | support_username | site_on (1/0) | support_on (1/0)\n\n"
        f"РўРµРєСѓС‰РёРµ: {ui.get('site_url','')} | {ui.get('support_link','')} | {int(ui.get('site_enabled', False))} | {int(ui.get('support_enabled', True))}"
    )
    await callback.answer()


@router.callback_query(F.data.in_({"toggle_site_link", "toggle_support_link"}))
async def toggle_public_link(callback: types.CallbackQuery, club: Club, club_settings: dict, session: AsyncSession, redis: Redis, is_owner: bool, is_super_admin: bool):
    if not (is_owner or is_super_admin):
        return await callback.answer("Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰С‘РЅ", show_alert=True)
    key = "site_enabled" if callback.data == "toggle_site_link" else "support_enabled"
    ui = club_settings.setdefault("ui", {})
    ui[key] = not ui.get(key, key == "support_enabled")
    await session.execute(update(Club).where(Club.id == club.id).values(club_settings=club_settings))
    await session.commit()
    await redis.delete(f"club_config:{callback.bot.token}")
    await callback.answer("вњ… РџРµСЂРµРєР»СЋС‡РµРЅРѕ")
    await admin_settings_menu(callback, club_settings, club.id)


@router.callback_query(F.data == "edit_site_url")
async def edit_site_url(callback: types.CallbackQuery, state: FSMContext, is_owner: bool, is_super_admin: bool):
    if not (is_owner or is_super_admin):
        return await callback.answer("Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰С‘РЅ", show_alert=True)
    await state.set_state(AdminSettingsSG.waiting_for_site_url)
    await callback.message.answer("РћС‚РїСЂР°РІСЊС‚Рµ URL СЃР°Р№С‚Р° (https://...):")
    await callback.answer()


@router.callback_query(F.data == "edit_support_username")
async def edit_support_username(callback: types.CallbackQuery, state: FSMContext, is_owner: bool, is_super_admin: bool):
    if not (is_owner or is_super_admin):
        return await callback.answer("Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰С‘РЅ", show_alert=True)
    await state.set_state(AdminSettingsSG.waiting_for_support_username)
    await callback.message.answer("РћС‚РїСЂР°РІСЊС‚Рµ Telegram username РїРѕРґРґРµСЂР¶РєРё, РЅР°РїСЂРёРјРµСЂ @admin:")
    await callback.answer()


@router.message(AdminSettingsSG.waiting_for_site_url)
async def save_site_url(message: types.Message, state: FSMContext, club: Club, club_settings: dict, session: AsyncSession, redis: Redis, is_owner: bool, is_super_admin: bool):
    if not (is_owner or is_super_admin):
        return await message.answer("Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰С‘РЅ")
    value = (message.text or "").strip()
    if not value.startswith(("https://", "http://")):
        return await message.answer("URL РґРѕР»Р¶РµРЅ РЅР°С‡РёРЅР°С‚СЊСЃСЏ СЃ https:// РёР»Рё http://")
    club_settings.setdefault("ui", {})["site_url"] = value
    await session.execute(update(Club).where(Club.id == club.id).values(club_settings=club_settings))
    await session.commit()
    await redis.delete(f"club_config:{message.bot.token}")
    await state.clear()
    await message.answer("вњ… РЎР°Р№С‚ СЃРѕС…СЂР°РЅС‘РЅ")


@router.message(AdminSettingsSG.waiting_for_support_username)
async def save_support_username(message: types.Message, state: FSMContext, club: Club, club_settings: dict, session: AsyncSession, redis: Redis, is_owner: bool, is_super_admin: bool):
    if not (is_owner or is_super_admin):
        return await message.answer("Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰С‘РЅ")
    value = (message.text or "").strip().lstrip("@")
    if not value or any(ch.isspace() for ch in value):
        return await message.answer("Р’РІРµРґРёС‚Рµ РєРѕСЂСЂРµРєС‚РЅС‹Р№ username Telegram")
    club_settings.setdefault("ui", {})["support_link"] = value
    await session.execute(update(Club).where(Club.id == club.id).values(club_settings=club_settings))
    await session.commit()
    await redis.delete(f"club_config:{message.bot.token}")
    await state.clear()
    await message.answer("вњ… Username РїРѕРґРґРµСЂР¶РєРё СЃРѕС…СЂР°РЅС‘РЅ")


@router.message(AdminSettingsSG.waiting_for_public_links)
async def admin_public_links_save(message: types.Message, state: FSMContext, club: Club, club_settings: dict, session: AsyncSession, redis: Redis):
    parts = [x.strip() for x in (message.text or "").split("|")]
    if len(parts) != 4 or parts[2] not in {"0", "1"} or parts[3] not in {"0", "1"}:
        return await message.answer("РќРµРІРµСЂРЅС‹Р№ С„РѕСЂРјР°С‚. РќСѓР¶РЅРѕ: СЃР°Р№С‚ | @РїРѕРґРґРµСЂР¶РєР° | 1/0 | 1/0")
    site, support = parts[0], parts[1].lstrip("@")
    if site and not site.startswith(("https://", "http://")):
        return await message.answer("РЎР°Р№С‚ РґРѕР»Р¶РµРЅ РЅР°С‡РёРЅР°С‚СЊСЃСЏ СЃ https:// РёР»Рё http://")
    club_settings.setdefault("ui", {}).update({"site_url": site, "support_link": support, "site_enabled": parts[2] == "1", "support_enabled": parts[3] == "1"})
    await session.execute(update(Club).where(Club.id == club.id).values(club_settings=club_settings))
    await session.commit()
    await redis.delete(f"club_config:{message.bot.token}")
    await state.clear()
    await message.answer("вњ… РЎР°Р№С‚ Рё РїРѕРґРґРµСЂР¶РєР° СЃРѕС…СЂР°РЅРµРЅС‹")


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
        return await callback.answer("вќЊ Р”РѕСЃС‚СѓРї РѕРіСЂР°РЅРёС‡РµРЅ.", show_alert=True)
    lock_key = f"lock:report:{club.id}"
    if await redis.get(lock_key):
        return await callback.answer("вЏі РЎРµРєСѓРЅРґСѓ, РґР°РЅРЅС‹Рµ Р·Р°РіСЂСѓР¶Р°СЋС‚СЃСЏ...", show_alert=False)
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
                human_name = config_disciplines.get(disc_key.lower(), {}).get("name", disc_key or "Р‘РµР· СЃРµРєС†РёРё")
                visits_by_discipline_text += f"рџҐ‹ {human_name}: <code>{count}</code>\n"
        top_discipline_name = "РќРµС‚ РґР°РЅРЅС‹С…"
        top_discipline = "РќРµС‚ РґР°РЅРЅС‹С…"
        if disc_visits_rows:
            top_disc_key, top_visits = max(disc_visits_rows, key=lambda item: item[1])
            top_discipline = top_visits
            top_discipline_name = config_disciplines.get((top_disc_key or "").lower(), {}).get("name", top_disc_key or "Р‘РµР· СЃРµРєС†РёРё")
        report_text = (
            f"рџ“Љ <b>Р”РќР•Р’РќРћР™ РћРўР§РЃРў: {club.name}</b>\n"
            f"рџ“… Р”Р°С‚Р°: <code>{now.strftime('%d.%m.%Y')}</code>\n\n"
            f"рџ’° РљР°СЃСЃР° СЃРµРіРѕРґРЅСЏ: <code>{revenue_today:.0f} в‚Ѕ</code>\n"
            f"вљ–пёЏ Р”РёРЅР°РјРёРєР° РєРѕ РІС‡РµСЂР°: <code>{revenue_today - revenue_yesterday:+.0f} в‚Ѕ</code>\n"
            f"рџҐ‹ Р’СЃРµРіРѕ Р°С‚Р»РµС‚РѕРІ: <code>{stats.total}</code>\n"
            f"рџ‘Ґ Р РѕРґРёС‚РµР»РµР№ СЃ РїСЂРёРІСЏР·РєРѕР№: <code>{stats.parents}</code>\n"
            f"рџљ¶вЂЌв™‚пёЏ РџРѕСЃРµС‰РµРЅРёР№: <code>{visits}</code>\n"
            f"рџ’Ћ Р”РµР№СЃС‚РІСѓСЋС‰РёС… Р°Р±РѕРЅРµРјРµРЅС‚РѕРІ: <code>{active_passes}</code>\n"
            f"вќЊ Р—Р°РєРѕРЅС‡РёР»СЃСЏ Р±Р°Р»Р°РЅСЃ: <code>{stats.expired}</code>\n"
            f"last_visit рџ’¤ РЎРїСЏС‰РёРµ (>14 РґРЅРµР№): <code>{stats.sleeping}</code>\n"
            f"рџҐ‹ Р“Р»Р°РІРЅРѕРµ РЅР°РїСЂР°РІР»РµРЅРёРµ: <code>{top_discipline_name}</code>\n\n"
            f"{visits_by_discipline_text}"
        )
        await callback.message.answer(report_text, parse_mode="HTML")
        await callback.answer()
    finally:
        await redis.delete(lock_key)


@router.callback_query(F.data == "admin_turnstile_main")
async def admin_turnstile_main(callback: types.CallbackQuery, club_settings: dict, is_owner: bool | None = None, is_super_admin: bool | None = None):
    if not (is_owner or is_super_admin):
        return await callback.answer("Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰С‘РЅ", show_alert=True)
    turnstile_config = club_settings.get("turnstile", {})
    is_enabled = turnstile_config.get("enabled", False)
    builder = InlineKeyboardBuilder()

    if not is_enabled:
        builder.row(types.InlineKeyboardButton(text="рџЄ› РќР°СЃС‚СЂРѕРёС‚СЊ Рё РІРєР»СЋС‡РёС‚СЊ", callback_data="setup_t_start"))
        builder.row(types.InlineKeyboardButton(text="рџ›  РќР°Р·Р°Рґ РІ РЅР°СЃС‚СЂРѕР№РєРё", callback_data="admin_settings"))
        await callback.message.edit_text(
            "рџ“Ў <b>РРЅС‚РµРіСЂР°С†РёСЏ РЎРљРЈР” (РўСѓСЂРЅРёРєРµС‚)</b>\n\n"
            "Р¤СѓРЅРєС†РёСЏ РѕС‚РєР»СЋС‡РµРЅР°.\n"
            "Р”Р»СЏ РїРѕРґРєР»СЋС‡РµРЅРёСЏ РІР°Рј РїРѕРЅР°РґРѕР±РёС‚СЃСЏ СЂРµР»Рµ DTWONDER (dingtian) Рё РЅР°СЃС‚СЂРѕРµРЅРЅС‹Р№ KeenDNS Р°РґСЂРµСЃ.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
    else:
        builder.row(types.InlineKeyboardButton(text="рџ”„ РР·РјРµРЅРёС‚СЊ РЅР°СЃС‚СЂРѕР№РєРё", callback_data="setup_t_start"))
        builder.row(types.InlineKeyboardButton(text="рџ›‘ Р’С‹РєР»СЋС‡РёС‚СЊ РЎРљРЈР”", callback_data="disable_t_confirm"))
        builder.row(types.InlineKeyboardButton(text="рџ›  РќР°Р·Р°Рґ РІ РЅР°СЃС‚СЂРѕР№РєРё", callback_data="admin_settings"))
        current_url = turnstile_config.get("base_url", "РќРµ Р·Р°РґР°РЅ")
        await callback.message.edit_text(
            f"рџ“Ў <b>РРЅС‚РµРіСЂР°С†РёСЏ РЎРљРЈР” (РўСѓСЂРЅРёРєРµС‚) Р°РєС‚РёРІРЅР°</b>\n\n"
            f"рџ“Њ РўРµРєСѓС‰РёР№ Р°РґСЂРµСЃ СЂРµР»Рµ: <code>{current_url}</code>\n\n"
            f"Р’С‹ РјРѕР¶РµС‚Рµ РёР·РјРµРЅРёС‚СЊ РїР°СЂР°РјРµС‚СЂС‹ РёР»Рё РѕС‚РєР»СЋС‡РёС‚СЊ РёРЅС‚РµРіСЂР°С†РёСЋ.",
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
    builder.row(types.InlineKeyboardButton(text="рџ›  РќР°Р·Р°Рґ РІ РЅР°СЃС‚СЂРѕР№РєРё", callback_data="admin_settings"))
    await callback.message.edit_text(
        "рџ“ќ <b>РЁР°Рі 1: Р’РІРµРґРёС‚Рµ Р°РґСЂРµСЃ KeenDNS (РёР»Рё IP)</b>\n\n"
        "вљ пёЏ РџСЂРѕС‚РѕРєРѕР» (http://) Рё РїРѕСЂС‚С‹ СѓРєР°Р·С‹РІР°С‚СЊ РЅРµ РЅСѓР¶РЅРѕ, Р±РѕС‚ РїРѕРґСЃС‚Р°РІРёС‚ РёС… СЃР°Рј.",
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
    builder.row(types.InlineKeyboardButton(text="вќЊ Р‘РµР· РїР°СЂРѕР»СЏ (РџСЂРѕРїСѓСЃС‚РёС‚СЊ)", callback_data="skip_t_password"))
    await message.answer(
        "рџ”ђ <b>РЁР°Рі 2: Р’РІРµРґРёС‚Рµ РїР°СЂРѕР»СЊ РѕС‚ РІРµР±-РїР°РЅРµР»Рё СЂРµР»Рµ</b>\n\n"
        "Р•СЃР»Рё РІС‹ СѓСЃС‚Р°РЅРѕРІРёР»Рё РїР°СЂРѕР»СЊ РЅР° РґРѕСЃС‚СѓРї Рє РїР»Р°С‚Рµ, С‚Рѕ РІРІРµРґРёС‚Рµ РµРіРѕ СЃРµР№С‡Р°СЃ РІ РѕС‚РІРµС‚РЅРѕРј СЃРѕРѕР±С‰РµРЅРёРё.\n"
        "Р•СЃР»Рё РЅР° РїР»Р°С‚Рµ РѕСЃС‚Р°Р»СЃСЏ СЃС‚Р°РЅРґР°СЂС‚РЅС‹Р№ РґРѕСЃС‚СѓРї Р±РµР· РїР°СЂРѕР»СЏ, РЅР°Р¶РјРёС‚Рµ РЅР° РєРЅРѕРїРєСѓ РЅРёР¶Рµ:",
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
            await callback.answer("рџ”’ РРЅС‚РµРіСЂР°С†РёСЏ РЎРљРЈР” СѓСЃРїРµС€РЅРѕ РѕС‚РєР»СЋС‡РµРЅР°", show_alert=True)
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
            await callback.answer("вќЊ РќРµ СѓРґР°Р»РѕСЃСЊ СЃРѕС…СЂР°РЅРёС‚СЊ РёР·РјРµРЅРµРЅРёСЏ РІ Р‘Р”", show_alert=True)
            return
        await admin_turnstile_main(callback, club_settings=current_settings)
    else:
        await callback.answer("РЎРљРЈР” Рё С‚Р°Рє РЅРµ Р±С‹Р» РЅР°СЃС‚СЂРѕРµРЅ", show_alert=True)


@router.callback_query(F.data == "manage_club_limits")
async def manage_club_limits_handler(callback: types.CallbackQuery, club: Club):
    club_settings = club.club_settings or {}
    limits = club_settings.get("limits", {})
    timeout = limits.get("session_timeout_minutes", 150)
    freeze_step = limits.get("freeze_days_step", 7)
    freeze_price = limits.get("freeze_price_per_day", 0)
    text = (
        f"вљ™пёЏ <b>РЈРїСЂР°РІР»РµРЅРёРµ Р»РёРјРёС‚Р°РјРё РєР»СѓР±Р° В«{club.name}В»</b>\n\n"
        f"вЏ± <b>РЎРµСЃСЃРёСЏ РІРёР·РёС‚Р° (РЎРљРЈР”):</b> <code>{timeout} РјРёРЅ.</code> ({timeout / 60:.1f} С‡.)\n"
        f"<i>Р’ С‚РµС‡РµРЅРёРµ СЌС‚РѕРіРѕ РІСЂРµРјРµРЅРё РїРѕРІС‚РѕСЂРЅС‹Рµ РїСЂРѕС…РѕРґС‹ С‡РµСЂРµР· С‚СѓСЂРЅРёРєРµС‚ РЅРµ СЃРїРёСЃС‹РІР°СЋС‚ Р·Р°РЅСЏС‚РёСЏ.</i>\n\n"
        f"вќ„пёЏ <b>РЁР°Рі Р·Р°РјРѕСЂРѕР·РєРё Р°Р±РѕРЅРµРјРµРЅС‚Р°:</b> <code>{freeze_step} РґРЅ.</code>\n"
        f"<i>РњРёРЅРёРјР°Р»СЊРЅС‹Р№ РїР°РєРµС‚ РґРЅРµР№, РЅР° РєРѕС‚РѕСЂС‹Р№ СЃРїРёСЃС‹РІР°РµС‚СЃСЏ Р·Р°РјРѕСЂРѕР·РєР°.</i>\n\n"
        f"рџ’і <b>РџР»Р°С‚РЅР°СЏ Р·Р°РјРѕСЂРѕР·РєР°:</b> <code>{freeze_price} в‚Ѕ/РґРµРЅСЊ</code>\n"
        f"<i>0 вЂ” РїРѕРєСѓРїРєР° Р·Р°РјРѕСЂРѕР·РєРё РѕС‚РєР»СЋС‡РµРЅР°.</i>\n\n"
        f"Р’С‹Р±РµСЂРёС‚Рµ РїР°СЂР°РјРµС‚СЂ РґР»СЏ РёР·РјРµРЅРµРЅРёСЏ:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="вЏ± РР·РјРµРЅРёС‚СЊ РІСЂРµРјСЏ СЃРµСЃСЃРёРё", callback_data="change_limit_session")],
        [InlineKeyboardButton(text="вќ„пёЏ РР·РјРµРЅРёС‚СЊ С€Р°Рі Р·Р°РјРѕСЂРѕР·РєРё", callback_data="change_limit_freeze")],
        [InlineKeyboardButton(text="рџ’і Р¦РµРЅР° 1 РґРЅСЏ Р·Р°РјРѕСЂРѕР·РєРё", callback_data="change_freeze_price")],
        [InlineKeyboardButton(text="в¬…пёЏ РќР°Р·Р°Рґ РІ РЅР°СЃС‚СЂРѕР№РєРё", callback_data="admin_settings")]
    ])
    await callback.message.edit_text(text=text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "change_limit_session")
async def change_limit_session(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminSettingsSG.waiting_for_session_timeout)
    await callback.message.answer("вЏ± <b>Р’РІРµРґРёС‚Рµ РЅРѕРІРѕРµ РІСЂРµРјСЏ СЃРµСЃСЃРёРё РІРёР·РёС‚Р° РІ РјРёРЅСѓС‚Р°С…</b> (РЅР°РїСЂРёРјРµСЂ, 120 РґР»СЏ 2 С‡Р°СЃРѕРІ):", parse_mode="HTML")
    await callback.answer()


@router.message(AdminSettingsSG.waiting_for_session_timeout)
async def process_session_timeout(message: types.Message, state: FSMContext, session: AsyncSession, club: Club, redis: Redis):
    if not message.text.isdigit():
        return await message.answer("вќЊ РћС€РёР±РєР°: Р’РІРµРґРёС‚Рµ С†РµР»РѕРµ С‡РёСЃР»Рѕ РјРёРЅСѓС‚!")
    minutes = int(message.text)
    if minutes < 1 or minutes > 1440:
        return await message.answer("вќЊ РћС€РёР±РєР°: Р’СЂРµРјСЏ СЃРµСЃСЃРёРё РґРѕР»Р¶РЅРѕ Р±С‹С‚СЊ РІ РґРёР°РїР°Р·РѕРЅРµ РѕС‚ 1 РґРѕ 1440 РјРёРЅСѓС‚ (24 С‡Р°СЃР°)!")
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
        logger.error(f"РћС€РёР±РєР° РїСЂРё СЃРѕС…СЂР°РЅРµРЅРёРё С‚Р°Р№РјР°СѓС‚Р° РЎРљРЈР”: {e}")
        await session.rollback()
        return await message.answer("вќЊ РќРµ СѓРґР°Р»РѕСЃСЊ СЃРѕС…СЂР°РЅРёС‚СЊ РёР·РјРµРЅРµРЅРёСЏ Р»РёРјРёС‚РѕРІ РІ Р‘Р”.")
    await state.clear()
    await message.answer(f"вњ… <b>Р’СЂРµРјСЏ РЎРљРЈР”-СЃРµСЃСЃРёРё СѓСЃРїРµС€РЅРѕ РёР·РјРµРЅРµРЅРѕ РЅР° {minutes} РјРёРЅСѓС‚!</b>", parse_mode="HTML")


@router.callback_query(F.data == "change_limit_freeze")
async def change_limit_freeze(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminSettingsSG.waiting_for_freeze_step)
    await callback.message.answer("вќ„пёЏ <b>Р’РІРµРґРёС‚Рµ РЅРѕРІС‹Р№ РјРёРЅРёРјР°Р»СЊРЅС‹Р№ С€Р°Рі Р·Р°РјРѕСЂРѕР·РєРё РІ РґРЅСЏС…</b> (РЅР°РїСЂРёРјРµСЂ, 7):", parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "configure_webapp_loading")
async def configure_webapp_loading(callback: types.CallbackQuery, state: FSMContext, club_settings: dict):
    ui = club_settings.get("ui", {}) or {}
    loading = ui.get("loading", {}) or {}
    await state.set_state(AdminSettingsSG.waiting_for_loading_config)
    await callback.message.answer(
        "рџЋЁ РћС‚РїСЂР°РІСЊС‚Рµ JSON РѕРґРЅРѕР№ СЃС‚СЂРѕРєРѕР№:\n"
        '<code>{"enabled":true,"logo_url":"https://.../logo.png","duration_ms":1200,"message":"Р—Р°РіСЂСѓР¶Р°РµРј РїСЂРёР»РѕР¶РµРЅРёРµвЂ¦"}</code>\n\n'
        f"РЎРµР№С‡Р°СЃ: {'РІРєР»СЋС‡С‘РЅ' if loading.get('enabled') else 'РІС‹РєР»СЋС‡РµРЅ'}.", parse_mode="HTML")
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
    await callback.answer("Р—Р°РіСЂСѓР·РѕС‡РЅС‹Р№ СЌРєСЂР°РЅ РІРєР»СЋС‡С‘РЅ" if loading["enabled"] else "Р—Р°РіСЂСѓР·РѕС‡РЅС‹Р№ СЌРєСЂР°РЅ РІС‹РєР»СЋС‡РµРЅ")
    await admin_settings_menu(callback, settings, club.id)


@router.callback_query(F.data == "upload_webapp_logo")
async def upload_webapp_logo(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminSettingsSG.waiting_for_loading_logo)
    await callback.message.answer("рџ–ј РћС‚РїСЂР°РІСЊС‚Рµ РёР·РѕР±СЂР°Р¶РµРЅРёРµ Р»РѕРіРѕС‚РёРїР° РѕРґРЅРёРј СЃРѕРѕР±С‰РµРЅРёРµРј.")
    await callback.answer()


@router.message(AdminSettingsSG.waiting_for_loading_logo, F.photo)
async def save_webapp_logo(message: types.Message, state: FSMContext, club: Club, session: AsyncSession, redis: Redis):
    if not message.photo or (message.photo[-1].file_size and message.photo[-1].file_size > 8 * 1024 * 1024):
        return await message.answer("вќЊ Р›РѕРіРѕС‚РёРї РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ РёР·РѕР±СЂР°Р¶РµРЅРёРµРј РЅРµ Р±РѕР»СЊС€Рµ 8 РњР‘.")
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
        ui["logo_url"] = f"/static/uploads/logos/{filename}"
        loading = dict(ui.get("loading") or {})
        loading["enabled"] = True
        loading.setdefault("duration_ms", 1200)
        loading.setdefault("message", "Р—Р°РіСЂСѓР¶Р°РµРј РїСЂРёР»РѕР¶РµРЅРёРµвЂ¦")
        ui["loading"] = loading
        settings["ui"] = ui
        db_club = await session.merge(club)
        db_club.club_settings = settings
        flag_modified(db_club, "club_settings")
        await session.commit()
    except Exception:
        await session.rollback()
        path.unlink(missing_ok=True)
        logger.exception("РћС€РёР±РєР° Р·Р°РіСЂСѓР·РєРё Р»РѕРіРѕС‚РёРїР° WebApp")
        return await message.answer("вќЊ РќРµ СѓРґР°Р»РѕСЃСЊ СЃРѕС…СЂР°РЅРёС‚СЊ Р»РѕРіРѕС‚РёРї. РџРѕРїСЂРѕР±СѓР№С‚Рµ РµС‰С‘ СЂР°Р·.")
    await redis.delete(f"club_config:{message.bot.token}")
    await state.clear()
    await message.answer("вњ… Р›РѕРіРѕС‚РёРї Р·Р°РіСЂСѓР¶РµРЅ Рё СЃРѕС…СЂР°РЅС‘РЅ.")


@router.message(AdminSettingsSG.waiting_for_loading_logo)
async def invalid_webapp_logo(message: types.Message):
    await message.answer("РћС‚РїСЂР°РІСЊС‚Рµ РёРјРµРЅРЅРѕ РёР·РѕР±СЂР°Р¶РµРЅРёРµ Р»РѕРіРѕС‚РёРїР° (С„РѕС‚Рѕ).")


@router.message(AdminSettingsSG.waiting_for_loading_config)
async def save_webapp_loading(message: types.Message, state: FSMContext, club: Club, session: AsyncSession, redis: Redis):
    try:
        value = json.loads(message.text or "")
        if not isinstance(value, dict):
            raise ValueError
        enabled = bool(value.get("enabled", False))
        logo_url = str(value.get("logo_url", "")).strip()
        duration = int(value.get("duration_ms", 1200))
        text = str(value.get("message", "Р—Р°РіСЂСѓР¶Р°РµРј РїСЂРёР»РѕР¶РµРЅРёРµвЂ¦")).strip()[:120]
        if duration < 300 or duration > 10000:
            return await message.answer("duration_ms РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ РѕС‚ 300 РґРѕ 10000.")
    except (ValueError, TypeError, json.JSONDecodeError):
        return await message.answer("РќРµРІРµСЂРЅС‹Р№ JSON. РџСЂРѕРІРµСЂСЊС‚Рµ РїСЂРёРјРµСЂ Рё РѕС‚РїСЂР°РІСЊС‚Рµ РµС‰С‘ СЂР°Р·.")
    settings = dict(club.club_settings or {})
    ui = dict(settings.get("ui") or {})
    ui["logo_url"] = logo_url
    ui["loading"] = {"enabled": enabled, "duration_ms": duration, "message": text}
    settings["ui"] = ui
    db_club = await session.merge(club)
    db_club.club_settings = settings
    flag_modified(db_club, "club_settings")
    await session.commit()
    await redis.delete(f"club_config:{message.bot.token}")
    await state.clear()
    await message.answer("вњ… РќР°СЃС‚СЂРѕР№РєРё Р·Р°РіСЂСѓР·РѕС‡РЅРѕРіРѕ СЌРєСЂР°РЅР° WebApp СЃРѕС…СЂР°РЅРµРЅС‹.")


@router.callback_query(F.data == "change_freeze_price")
async def change_freeze_price(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminSettingsSG.waiting_for_freeze_price)
    await callback.message.answer("рџ’і Р’РІРµРґРёС‚Рµ С†РµРЅСѓ РѕРґРЅРѕРіРѕ РґРЅСЏ Р·Р°РјРѕСЂРѕР·РєРё РІ СЂСѓР±Р»СЏС… (0 вЂ” РѕС‚РєР»СЋС‡РёС‚СЊ):")
    await callback.answer()


@router.message(AdminSettingsSG.waiting_for_freeze_price)
async def process_freeze_price(message: types.Message, state: FSMContext, club: Club, session: AsyncSession, redis: Redis):
    try:
        price = float((message.text or "").replace(",", ".").strip())
    except ValueError:
        return await message.answer("Р’РІРµРґРёС‚Рµ С‡РёСЃР»Рѕ, РЅР°РїСЂРёРјРµСЂ 150 РёР»Рё 0.")
    if price < 0 or price > 100000:
        return await message.answer("Р¦РµРЅР° РґРѕР»Р¶РЅР° Р±С‹С‚СЊ РѕС‚ 0 РґРѕ 100000 в‚Ѕ.")
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
        logger.exception("РћС€РёР±РєР° СЃРѕС…СЂР°РЅРµРЅРёСЏ С†РµРЅС‹ РїР»Р°С‚РЅРѕР№ Р·Р°РјРѕСЂРѕР·РєРё")
        return await message.answer("вќЊ РќРµ СѓРґР°Р»РѕСЃСЊ СЃРѕС…СЂР°РЅРёС‚СЊ С†РµРЅСѓ. РџРѕРїСЂРѕР±СѓР№С‚Рµ РµС‰С‘ СЂР°Р·.")
    await redis.delete(f"club_config:{message.bot.token}")
    await state.clear()
    await message.answer(f"вњ… Р¦РµРЅР° РїР»Р°С‚РЅРѕР№ Р·Р°РјРѕСЂРѕР·РєРё: {price:g} в‚Ѕ Р·Р° РґРµРЅСЊ.")


@router.message(AdminSettingsSG.waiting_for_freeze_step)
async def process_freeze_step(message: types.Message, state: FSMContext, session: AsyncSession, club: Club, redis: Redis):
    if not message.text.isdigit():
        return await message.answer("вќЊ РћС€РёР±РєР°: Р’РІРµРґРёС‚Рµ С†РµР»РѕРµ С‡РёСЃР»Рѕ РґРЅРµР№!")
    days = int(message.text)
    if days < 1 or days > 30:
        return await message.answer("вќЊ РћС€РёР±РєР°: РЁР°Рі Р·Р°РјРѕСЂРѕР·РєРё РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ РѕС‚ 1 РґРѕ 30 РґРЅРµР№!")
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
        logger.error(f"РћС€РёР±РєР° РїСЂРё СЃРѕС…СЂР°РЅРµРЅРёРё С€Р°РіР° Р·Р°РјРѕСЂРѕР·РєРё: {e}")
        await session.rollback()
        return await message.answer("вќЊ РќРµ СѓРґР°Р»РѕСЃСЊ СЃРѕС…СЂР°РЅРёС‚СЊ РёР·РјРµРЅРµРЅРёСЏ С€Р°РіР° Р·Р°РјРѕСЂРѕР·РєРё РІ Р‘Р”.")
    await state.clear()
    await message.answer(f"вњ… <b>РњРёРЅРёРјР°Р»СЊРЅС‹Р№ С€Р°Рі Р·Р°РјРѕСЂРѕР·РєРё СѓСЃРїРµС€РЅРѕ РёР·РјРµРЅРµРЅ РЅР° {days} РґРЅРµР№!</b>", parse_mode="HTML")


