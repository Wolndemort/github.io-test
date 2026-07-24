from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
import copy
from datetime import time, datetime
from sqlalchemy import func, and_, or_
from services.gate_control import process_athlete_gate_pass
from sqlalchemy.orm.attributes import flag_modified
from handlers.skud import save_and_test_turnstile
from handlers.states import AdminStates, AdminSettings, TurnstileSetup, AdminTariffStates, AdminScheduleStates, \
    YooKassaSetupStates, AdminSettingsSG
from redis.asyncio import Redis
from sqlalchemy import update
import pandas as pd
import os
from handlers.buttons import get_scanner_keyboard
from aiogram.types import FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database.db import get_all_users_count, get_total_athletes_count, get_active_subs_count, User, get_daily_stats, Student, \
    Club, PaymentOrder, CartOrder, VisitLog, ClubStaff
from services.analytics import reporting_periods
from sqlalchemy import select
from handlers.buttons import admin_keyboard
from handlers.states import AdminManualAdd
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from datetime import timedelta
import re
from aiogram import Router, F, types
import asyncio
import json
from uuid import uuid4
from pathlib import Path
from loguru import logger
from PIL import Image, UnidentifiedImageError
from services.input_normalization import normalize_ru_phone, parse_user_date
from services.staff_permissions import permissions_for_staff


router = Router()


def _manual_phone_key(value: str | None) -> str:
    return normalize_ru_phone(value) or ""


@router.callback_query(F.data == "admin_add_manual")
async def start_manual_add(
        callback: types.CallbackQuery,
        state: FSMContext,
        is_owner: bool,
        is_super_admin: bool,
        is_staff: bool,
):
    if not (is_owner or is_super_admin):
        return await callback.answer("Доступ только для администратора клуба.", show_alert=True)
    await state.clear()
    await state.set_state(AdminManualAdd.waiting_for_name)
    await callback.message.answer(
        "🆕 <b>Добавление атлета</b>\n\nВведите имя и фамилию атлета:",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminManualAdd.waiting_for_name)
async def manual_add_name(message: types.Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name.split()) < 2:
        return await message.answer("Введите имя и фамилию через пробел.")
    await state.update_data(athlete_name=name)
    await state.set_state(AdminManualAdd.waiting_for_phone)
    await message.answer("Введите номер телефона атлета, например: +7 999 111-22-33")


@router.message(AdminManualAdd.waiting_for_phone)
async def manual_add_phone(message: types.Message, state: FSMContext):
    phone = (message.text or "").strip()
    if len(_manual_phone_key(phone)) < 10:
        return await message.answer("Номер должен содержать минимум 10 цифр. Попробуйте ещё раз.")
    normalized_phone = normalize_ru_phone(phone)
    if not normalized_phone:
        return await message.answer("Введите российский номер из 10 или 11 цифр.")
    await state.update_data(parent_phone=normalized_phone)
    await state.set_state(AdminManualAdd.waiting_for_birthday)
    await message.answer("Введите дату рождения в формате ДД.ММ.ГГГГ или отправьте 0, если дата неизвестна.")


@router.message(AdminManualAdd.waiting_for_birthday)
async def manual_add_birthday(message: types.Message, state: FSMContext, club_settings: dict):
    value = (message.text or "").strip()
    birthday = None
    if value != "0":
        try:
            birthday = parse_user_date(value)
        except ValueError:
            return await message.answer("Введите корректную дату ДД.ММ.ГГГГ или 0.")
    await state.update_data(birthday=birthday.isoformat() if birthday else None)
    disciplines = {
        code: info for code, info in (club_settings.get("disciplines", {}) or {}).items()
        if info.get("active", True)
    }
    builder = InlineKeyboardBuilder()
    for code, info in disciplines.items():
        builder.row(InlineKeyboardButton(text=info.get("name", code), callback_data=f"admin_manual_disc_{code}"))
    if not disciplines:
        await state.clear()
        return await message.answer("В клубе нет активных дисциплин для создания атлета.")
    await state.set_state(AdminManualAdd.waiting_for_discipline)
    await message.answer("Выберите дисциплину:", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("admin_manual_disc_"), AdminManualAdd.waiting_for_discipline)
async def manual_add_discipline(callback: types.CallbackQuery, state: FSMContext, club_settings: dict):
    discipline = callback.data.removeprefix("admin_manual_disc_")
    config = (club_settings.get("disciplines", {}) or {}).get(discipline)
    if not config or not config.get("active", True):
        return await callback.answer("Дисциплина недоступна.", show_alert=True)
    await state.update_data(discipline=discipline)
    builder = InlineKeyboardBuilder()
    for index, tariff in enumerate(config.get("tariffs", []) or []):
        count = "♾" if tariff.get("count") == 999 else str(tariff.get("count", 0))
        builder.row(InlineKeyboardButton(
            text=f"{count} зан. / {tariff.get('days', 30)} дн. — {tariff.get('price', 0)} ₽",
            callback_data=f"admin_manual_tariff_{discipline}_{index}",
        ))
    builder.row(InlineKeyboardButton(text="Без абонемента", callback_data=f"admin_manual_no_sub_{discipline}"))
    await state.set_state(AdminManualAdd.waiting_for_tariff)
    await callback.message.edit_text("Выберите тариф или вариант без абонемента:", reply_markup=builder.as_markup())
    await callback.answer()


async def _finish_manual_add(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession,
                             club: Club, discipline: str, tariff_idx: int | None):
    data = await state.get_data()
    config = (club.club_settings or {}).get("disciplines", {}).get(discipline, {})
    tariffs = config.get("tariffs", []) or []
    count = 0
    expire_date = None
    if tariff_idx is not None:
        if tariff_idx < 0 or tariff_idx >= len(tariffs):
            return await callback.answer("Тариф не найден.", show_alert=True)
        tariff = tariffs[tariff_idx]
        count = int(tariff.get("count", 0) or 0)
        expire_date = datetime.now() + timedelta(days=int(tariff.get("days", 30) or 30))

    name = data.get("athlete_name", "").strip()
    birthday = parse_user_date(data.get("birthday"))
    phone = data.get("parent_phone")
    students = (await session.execute(select(Student).where(Student.club_id == club.id))).scalars().all()
    duplicate = next((student for student in students
                      if student.name.strip().casefold() == name.casefold()
                      and student.birthday == birthday
                      and (student.discipline or "").casefold() == discipline.casefold()
                      and _manual_phone_key(student.parent_phone) == _manual_phone_key(phone)), None)
    if duplicate:
        await state.clear()
        await callback.message.answer("⚠️ Такой атлет уже есть в базе клуба. Новая запись не создана.")
        return await callback.answer()

    session.add(Student(
        parent_id=None,
        club_id=club.id,
        name=name,
        parent_phone=phone,
        birthday=birthday,
        expire_date=expire_date,
        balance_lessons=count,
        can_freeze=1,
        is_frozen=0,
        discipline=discipline,
    ))
    await session.commit()
    await state.clear()
    await callback.message.answer("✅ Атлет успешно добавлен в базу клуба.")
    await callback.answer()


@router.callback_query(F.data.startswith("admin_manual_tariff_"), AdminManualAdd.waiting_for_tariff)
async def manual_add_tariff(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, club: Club):
    raw = callback.data.removeprefix("admin_manual_tariff_")
    discipline, raw_index = raw.rsplit("_", 1)
    try:
        tariff_idx = int(raw_index)
    except ValueError:
        return await callback.answer("Некорректный тариф.", show_alert=True)
    await _finish_manual_add(callback, state, session, club, discipline, tariff_idx)


@router.callback_query(F.data.startswith("admin_manual_no_sub_"), AdminManualAdd.waiting_for_tariff)
async def manual_add_without_subscription(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, club: Club):
    discipline = callback.data.removeprefix("admin_manual_no_sub_")
    await _finish_manual_add(callback, state, session, club, discipline, None)


@router.callback_query(F.data == "admin_quick_athletes")
async def admin_quick_athletes(
    callback: types.CallbackQuery,
    session: AsyncSession,
    club: Club,
):
    students = (await session.execute(
        select(Student).where(Student.club_id == club.id).order_by(Student.name)
    )).scalars().all()
    parent_ids = {s.parent_id for s in students if s.parent_id}
    parents = {}
    if parent_ids:
        parent_rows = (await session.execute(select(User).where(User.user_id.in_(parent_ids)))).scalars().all()
        parents = {u.user_id: u.full_name for u in parent_rows}
    if not students:
        return await callback.answer("В клубе пока нет атлетов.", show_alert=True)

    lines = [f"👥 <b>Атлеты клуба: {club.name}</b>", f"Всего: <b>{len(students)}</b>\n"]
    for number, student in enumerate(students, 1):
        balance = "безлимит" if student.balance_lessons == 999 else str(student.balance_lessons or 0)
        expire = student.expire_date.strftime("%d.%m.%Y") if student.expire_date else "не указан"
        parent = parents.get(student.parent_id, "не привязан") if student.parent_id else "не привязан"
        if student.is_frozen:
            status = "❄️ заморожен"
        elif student.expire_date and student.expire_date > datetime.now():
            status = "✅ активен"
        else:
            status = "⚠️ истёк"
        lines.append(
            f"<b>{number}. {student.name}</b> — {status}\n"
            f"   Родитель: {parent}\n"
            f"   Дисциплина: {student.discipline or 'не указана'}\n"
            f"   Баланс: {balance} | До: {expire}"
        )
    text = "\n".join(lines)
    chunks = [text[i:i + 3800] for i in range(0, len(text), 3800)]
    await callback.answer()
    for chunk in chunks:
        await callback.message.answer(chunk, parse_mode="HTML")


@router.message(Command('admin'))
@router.callback_query(F.data == "admin")
async def admin_panel(
        event: types.Message | types.CallbackQuery,
        club: Club,
        club_settings: dict,
        is_owner: bool,
        is_super_admin: bool,
        is_staff: bool,
        staff,
        session: AsyncSession,
        state: FSMContext
):
    if not (is_owner or is_super_admin or is_staff):
        return await (event.answer("Доступ запрещён", show_alert=True) if isinstance(event, types.CallbackQuery) else event.answer("Доступ запрещён"))

    await state.clear()

    message = event.message if isinstance(event, types.CallbackQuery) else event

    if isinstance(event, types.CallbackQuery):
        await event.answer()

    try:
        all_users = await get_all_users_count(club_id=club.id, session=session)
        total_athletes = await get_total_athletes_count(club_id=club.id, session=session)
        active_subs = await get_active_subs_count(club_id=club.id, session=session)
        club_name = club.name or "Клуб"

        sub_end = club.subscription_expire_at
        if sub_end:
            days_left = (sub_end.replace(tzinfo=None) - datetime.now().replace(tzinfo=None)).days
            sub_info = f"<code>до {sub_end.strftime('%d.%m.%Y')} ({max(0, days_left)} дн.)</code>"
        else:
            sub_info = "<code>не активна</code>"

        text = (
            f"📈 <b>Панель управления: {club_name}</b>\n\n"
            f"🔐 Подписка CRM: {sub_info}\n"
            f"🥋 Всего атлетов: <code>{total_athletes}</code>\n"
            f"👥 Родителей с привязкой: <code>{all_users}</code>\n"
            f"💳 Активных абонементов: <code>{active_subs}</code>\n\n"
            "Чего желаете, босс?"
        )

        await message.answer(
            text=text,
            reply_markup=admin_keyboard(
                club.id,  # 1. ID клуба
                club_settings,  # 2. Настройки
                club.subscription_expire_at,  # 3. Дата напрямую из БД
                staff_permissions=permissions_for_staff(staff) if staff else None
            ),
            parse_mode="HTML"
        )

        await message.answer(
            text="📸 Нативная панель СКУД активирована внизу экрана.",
            reply_markup=get_scanner_keyboard(club_id=club.id)
        )

    except Exception as e:
        logger.error(f"❌ Критическая ошибка в admin_panel для клуба {club.id}: {e}", exc_info=True)


@router.callback_query(F.data == "back_to_admin")
async def back_to_admin_main_menu(
        callback: types.CallbackQuery,
        club: Club,
        club_settings: dict,
        is_owner: bool,
        is_super_admin: bool
):
    if not (is_owner or is_super_admin):
        return await callback.answer("❌ Доступ ограничен.", show_alert=True)

    club_name = club.name or "Клуб"

    await callback.message.edit_text(
        text=f"⚙️ <b>Панель управления: {club_name}</b>\nВыберите нужный раздел:",
        reply_markup=admin_keyboard(
            club.id,  # 1. ID клуба
            club_settings,  # 2. Настройки
            club.subscription_expire_at,  # 3. Дата напрямую из БД
            staff_permissions=permissions_for_staff(staff) if staff else None
        ),
        parse_mode="HTML"
    )
    await callback.answer()

    await callback.message.answer(
        text="📸 Панель СКУД активирована внизу экрана.",
        reply_markup=get_scanner_keyboard(club_id=club.id)
    )



@router.callback_query(F.data == "admin_settings")
async def admin_settings_menu(callback: types.CallbackQuery, club_settings: dict, club_id: int, is_owner: bool | None = None, is_super_admin: bool | None = None):
    if is_owner is False and is_super_admin is False:
        return await callback.answer("Доступ запрещён: настройки клуба доступны только главному администратору.", show_alert=True)
    builder = InlineKeyboardBuilder()
    features = club_settings.get("features", {})
    limits = club_settings.get("limits", {})

    # Список системных кнопок переключателей модулей
    buttons = {
        "freeze": "Заморозка",
        "qr_checkin": "QR-вход",
        "manual_add": "Ручное добавление",
        "online_payments": "Онлайн-платежи"
    }

    for key, label in buttons.items():
        status = "✅" if features.get(key, False) else "❌"
        builder.row(types.InlineKeyboardButton(
            text=f"{status} {label}",
            callback_data=f"toggle_feat_{key}")
        )

    # Настройки магазина доступны всегда: сначала админ указывает ключи,
    # затем при необходимости включает online_payments.
    builder.row(types.InlineKeyboardButton(
        text="🛍 Настройки магазина / YooKassa",
        callback_data="admin_setup_yookassa"
    ))
    builder.row(types.InlineKeyboardButton(
        text="🛒 Управление товарами",
        web_app=types.WebAppInfo(url=f"https://{club_id}.speedycrm.ru/webapp/admin-products?club_id={club_id}")
    ))
    builder.row(types.InlineKeyboardButton(
        text="💵 Продажа товаров за наличные",
        web_app=types.WebAppInfo(url=f"https://{club_id}.speedycrm.ru/webapp/admin-product-sale?club_id={club_id}")
    ))
    builder.row(types.InlineKeyboardButton(
        text="💰 Касса и отчёт по наличным",
        web_app=types.WebAppInfo(url=f"https://{club_id}.speedycrm.ru/admin/cash?club_id={club_id}")
    ))
    ui = club_settings.get("ui", {})
    site_mark = "✅" if ui.get("site_enabled", False) else "❌"
    support_mark = "✅" if ui.get("support_enabled", True) else "❌"
    builder.row(types.InlineKeyboardButton(text=f"{site_mark} Сайт клуба", callback_data="toggle_site_link"), types.InlineKeyboardButton(text=f"{support_mark} Поддержка", callback_data="toggle_support_link"))
    builder.row(types.InlineKeyboardButton(text="✏️ Изменить сайт", callback_data="edit_site_url"), types.InlineKeyboardButton(text="✏️ Изменить username", callback_data="edit_support_username"))

    # ⚙️ НАША НОВАЯ КНОПКА: Переход в меню изменения сессий СКУД и шага заморозок
    builder.row(types.InlineKeyboardButton(
        text="⚙️ Настройка лимитов клуба",
        callback_data="manage_club_limits"  # 👈 Тот самый колбэк, который ведёт на новое меню!
    ))
    loading_enabled = bool((ui.get("loading") or {}).get("enabled", False))
    loading_mark = "✅" if loading_enabled else "❌"
    builder.row(
        types.InlineKeyboardButton(text=f"{loading_mark} Загрузочный экран", callback_data="toggle_webapp_loading"),
        types.InlineKeyboardButton(text="⚙️ Настроить", callback_data="configure_webapp_loading"),
    )
    builder.row(types.InlineKeyboardButton(text="🖼 Загрузить логотип WebApp", callback_data="upload_webapp_logo"))

    builder.row(types.InlineKeyboardButton(
        text="💰 Настройка тарифов",
        callback_data="admin_tariffs_sections"
    ))
    builder.row(types.InlineKeyboardButton(
        text="💰 Тарифы (WebApp)",
        web_app=types.WebAppInfo(url=f"https://{club_id}.speedycrm.ru/webapp/admin-tariffs?club_id={club_id}")
    ))

    builder.row(types.InlineKeyboardButton(
        text="💳 Изменить реквизиты",
        callback_data="admin_edit_payments"
    ))

    turnstile_config = club_settings.get("turnstile", {})
    t_status = "✅" if turnstile_config.get("enabled", False) else "❌"
    builder.row(types.InlineKeyboardButton(
        text=f"{t_status} СКУД(Турникет)", callback_data='admin_turnstile_main'))

    builder.row(types.InlineKeyboardButton(text="🥋 Управление секциями", callback_data="manage_disciplines"))
    builder.row(types.InlineKeyboardButton(text="⬅️ В админ-панель", callback_data="admin"))

    await callback.message.edit_text(
        "🛠 <b>Настройки модулей клуба</b>\n\nВключайте и выключайте функции бота:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )



@router.callback_query(F.data == "manage_disciplines")
async def manage_disciplines_menu(callback: types.CallbackQuery, club_settings: dict):
    # Достаем дисциплины, если их нет — будет пустой словарь
    disciplines = club_settings.get("disciplines", {})
    builder = InlineKeyboardBuilder()

    # Если секций еще нет в базе этого клуба
    if not disciplines:
        builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_settings"))
        return await callback.message.edit_text(
            "🥋 <b>Список направлений пуст</b>\n\nОбратитесь к супер-админу для настройки базы секций.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )

    for code, info in disciplines.items():
        # Используем .get() для безопасности
        status = "✅" if info.get("active") else "❌"
        name = info.get("name", code.upper())

        builder.row(types.InlineKeyboardButton(
            text=f"{status} {name}",
            callback_data=f"toggle_disc_{code}")
        )

    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_settings"))

    await callback.message.edit_text(
        "🥋 <b>Список направлений</b>\n\nОтметьте секции, которые работают в вашем клубе:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("toggle_feat_") | F.data.startswith("toggle_disc_"))
async def toggle_logic(
        callback: types.CallbackQuery,
        club: Club,
        club_settings: dict,
        session: AsyncSession,
        redis: Redis
):
    parts = callback.data.split("_")
    action_type = parts[1]  # 'feat' или 'disc'

    # Собираем ключ из всех оставшихся частей после 'toggle' и 'type'
    # Это склеит 'qr' и 'checkin' обратно в 'qr_checkin'
    target_key = "_".join(parts[2:])

    if action_type == "feat":
        features = club_settings.setdefault("features", {})
        # Важно: берем значение, инвертируем и записываем обратно
        current = features.get(target_key, True)
        features[target_key] = not current

    elif action_type == "disc":
        disciplines = club_settings.setdefault("disciplines", {})
        disc_info = disciplines.get(target_key)
        if disc_info:
            disc_info["active"] = not disc_info.get("active", True)
        else:
            return await callback.answer(f"Ошибка: {target_key} не найден", show_alert=True)

    # 💾 Сохраняем (тут всё четко)
    await session.execute(
        update(Club)
        .where(Club.id == club.id)
        .values(club_settings=club_settings)
    )
    await session.commit()

    # Чистим кэш
    await redis.delete(f"club_config:{callback.bot.token}")

    await callback.answer("✅ Настройки обновлены")

    # Перерисовываем меню с ОБНОВЛЕННЫМ словарем
    if action_type == "feat":
        await admin_settings_menu(callback, club_settings, club.id)
    else:
        await manage_disciplines_menu(callback, club_settings)


@router.callback_query(F.data == "admin_broadcast")
async def start_broadcast(
        callback: types.CallbackQuery,
        state: FSMContext,
        is_owner: bool,  # <--- Исправил под мидлварь
        is_super_admin: bool  # <--- Исправил под мидлварь
):
    # Проверка прав (SaaS стиль)
    if not (is_owner or is_super_admin):
        return await callback.answer("❌ У вас нет прав администратора.", show_alert=True)

    await state.set_state(AdminStates.waiting_for_broadcast_text)

    # Добавим кнопку отмены, чтобы админ не "завис" в стейте
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Отмена", callback_data="admin")  # Возврат в админку

    await callback.message.answer(
        "📝 <b>Режим рассылки по вашему клубу</b>\n\n"
        "Отправьте сообщение (текст, фото или видео).\n"
        "Бот перешлет его <b>всем атлетам</b> вашего клуба.",
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )
    await callback.answer()


@router.message(AdminStates.waiting_for_broadcast_text)
async def process_broadcast(message: types.Message, state: FSMContext, session: AsyncSession, club: Club):
    # 1. Берем юзеров ТОЛЬКО этого клуба
    stmt = select(User.user_id).where(User.club_id == club.id)
    result = await session.execute(stmt)
    user_ids = result.scalars().all()

    # 2. Рассылаем (через copy_message, чтобы сохранить медиа)
    count = 0
    for uid in user_ids:
        try:
            await message.copy_to(chat_id=uid)
            count += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass

    await message.answer(f"✅ Рассылка завершена! Получили: {count} чел.")
    await state.clear()


@router.callback_query(F.data == 'export_db')
async def export_database(
    callback: types.CallbackQuery,
    session: AsyncSession,
    club: Club,              # <--- Исправлено (объект из мидлвари)
    is_owner: bool,          # <--- Исправлено
    is_super_admin: bool     # <--- Исправлено
):
    # Проверка прав
    if not (is_owner or is_super_admin):
        return await callback.answer("❌ Нет прав.", show_alert=True)

    # Путь к файлу (лучше в /tmp для Docker)
    file_path = f"export_club_{club.id}_{datetime.now().strftime('%H%M%S')}.csv"
    await callback.answer("⏳ Формирую отчет...")

    try:
        # 🛡️ ИЗОЛЯЦИЯ: Используем club.id
        users_res = await session.execute(select(User).where(User.club_id == club.id))
        students_res = await session.execute(select(Student).where(Student.club_id == club.id))

        users = users_res.scalars().all()
        students = students_res.scalars().all()

        if not students:
            return await callback.message.answer("📭 В базе вашего клуба пока нет атлетов.")

        # Собираем данные (у тебя тут всё чётко)
        users_dict = {u.user_id: u.full_name for u in users}
        students_data = [
            {
                "Имя атлета": s.name,
                "Родитель": users_dict.get(s.parent_id, "Не найден"),
                "Срок до": s.expire_date.strftime('%d.%m.%Y') if s.expire_date else "Нет",
                "Заморожен": "Да" if s.is_frozen else "Нет",
                "Баланс занятий": s.balance_lessons
            }
            for s in students
        ]

        df = pd.DataFrame(students_data)
        # utf-8-sig важен для корректного открытия в Excel на Windows
        df.to_csv(file_path, index=False, encoding='utf-8-sig')

        await callback.message.answer_document(
            FSInputFile(file_path),
            caption=f"📊 <b>Экспорт базы: {club.name}</b>\n👥 Атлетов: {len(students)}"
        )

    except Exception as e:
        logger.error(f"❌ Ошибка экспорта клуба {club.id}: {e}")
        await callback.answer("❌ Ошибка формирования файла", show_alert=True)
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


@router.callback_query(F.data == 'daily_report')
async def show_daily_report(
        callback: types.CallbackQuery,
        club,  # Из нашей оптимизированной мидлвари (CleanClubContext)
        club_settings: dict,  # Из нашей оптимизированной мидлвари
        is_owner: bool,
        is_super_admin: bool,
        session: AsyncSession,
        redis  # Передается из мидлвари для контроля спама кнопкой
):
    # 1. Жесткая проверка прав доступа
    if not (is_owner or is_super_admin):
        return await callback.answer("❌ Доступ ограничен.", show_alert=True)

    # 2. Anti-spam защита (Rate Limit на 5 секунд), чтобы админы не ложили БД частыми кликами
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

        # 3. Базовые визиты и действующие абонементы
        visits, active_passes = await get_daily_stats(club_id=club.id, session=session)

        # 4. Касса за СЕГОДНЯ (сумма в копейках переводится в рубли)
        today_rev_res = await session.execute(
            select(func.coalesce(func.sum(PaymentOrder.amount_kopecks), 0)).where(
                and_(
                    PaymentOrder.club_id == club.id,
                    PaymentOrder.status == "CONFIRMED",
                    PaymentOrder.created_at >= start_of_today
                )
            )
        )
        today_cart_res = await session.execute(
            select(func.coalesce(func.sum(CartOrder.amount_kopecks), 0)).where(
                CartOrder.club_id == club.id,
                CartOrder.status == "CONFIRMED",
                CartOrder.created_at >= start_of_today,
            )
        )
        revenue_today = (today_rev_res.scalar_one() + today_cart_res.scalar_one()) / 100

        # 5. Касса за ВЧЕРА (для расчета динамики)
        yesterday_rev_res = await session.execute(
            select(func.coalesce(func.sum(PaymentOrder.amount_kopecks), 0)).where(
                and_(
                    PaymentOrder.club_id == club.id,
                    PaymentOrder.status == "CONFIRMED",
                    PaymentOrder.created_at >= start_of_yesterday,
                    PaymentOrder.created_at < start_of_today
                )
            )
        )
        yesterday_cart_res = await session.execute(
            select(func.coalesce(func.sum(CartOrder.amount_kopecks), 0)).where(
                CartOrder.club_id == club.id,
                CartOrder.status == "CONFIRMED",
                CartOrder.created_at >= start_of_yesterday,
                CartOrder.created_at < start_of_today,
            )
        )
        revenue_yesterday = (yesterday_rev_res.scalar_one() + yesterday_cart_res.scalar_one()) / 100

        # 6. Общее число клиентов, просроченные и спящие одним легким SQL-запросом
        stats_res = await session.execute(
            select(
                func.count(Student.id).label("total"),
                 func.count(Student.id).filter(and_(
                     func.coalesce(Student.is_frozen, 0) == 0,
                     or_(
                         Student.balance_lessons <= 0,
                         Student.expire_date.is_(None),
                         Student.expire_date <= now,
                     ),
                 )).label("expired"),
                 func.count(Student.id).filter(
                    or_(Student.last_visit.is_(None), Student.last_visit <= sleeping_threshold)
                 ).label("sleeping"),
                 func.count(func.distinct(Student.parent_id)).filter(Student.parent_id.is_not(None)).label("parents")
            ).where(Student.club_id == club.id)
        )
        stats = stats_res.one()

        # 7. 🥋 СЧИТАЕМ РЕАЛЬНЫЕ ВИЗИТЫ ПО СЕКЦИЯМ ЗА СЕГОДНЯ (Группировка SQL)
        disc_visits_res = await session.execute(
            select(Student.discipline, func.count(VisitLog.id))
            .join(Student, VisitLog.student_id == Student.id)
            .where(
                VisitLog.club_id == club.id,
                VisitLog.visited_at >= start_of_today,
                VisitLog.visited_at < start_of_today + timedelta(days=1),
            )
            .group_by(Student.discipline)
        )
        disc_visits_rows = disc_visits_res.all()

        # Собираем красивый текстовый блок с разбивкой по дисциплинам
        config_disciplines = club_settings.get("disciplines", {})
        visits_by_discipline_text = ""

        if disc_visits_rows:
            for disc_key, count in disc_visits_rows:
                if not disc_key:
                    human_name = "Не определено"
                else:
                    human_name = config_disciplines.get(str(disc_key).lower(), {}).get("name", disc_key)

                visits_by_discipline_text += f" • {human_name}: <code>{count} чел.</code>\n"
        else:
            visits_by_discipline_text = " • <i>Пока никто не чекинился сегодняшним числом</i>\n"

        # 8. Расчет текстового индикатора динамики кассы
        if revenue_today > revenue_yesterday:
            revenue_diff_text = f"🟢 +{revenue_today - revenue_yesterday:,.0f} ₽ (Выше вчера)"
        elif revenue_today < revenue_yesterday:
            revenue_diff_text = f"🔴 -{revenue_yesterday - revenue_today:,.0f} ₽ (Ниже вчера)"
        else:
            revenue_diff_text = "⚪️ На уровне вчера"

        # 9. Сборка финального премиального интерфейса отчета для директора
        report_text = (
            f"📊 <b>БИЗНЕС-ОТЧЕТ: {club.name}</b>\n"
            f"📅 Дата: <code>{now.strftime('%d.%m.%Y')}</code>\n\n"
            f"💰 <b>Касса сегодня:</b> <code>{revenue_today:,.0f} ₽</code>\n"
            f"⚖️ <b>Динамика:</b> <code>{revenue_diff_text}</code>\n"
            f"🥋 <b>Атлетов в базе:</b> <code>{stats.total}</code>\n"
            f"👥 <b>Родителей с привязкой:</b> <code>{stats.parents}</code>\n\n"
            f"📈 <b>АКТИВНОСТЬ ЗА ДЕНЬ:</b>\n"
            f"🚶‍♂️ Всего визитов: <code>{visits}</code>\n"
            f"💎 Активных абонементов: <code>{active_passes}</code>\n\n"
            f"🥋 <b>ВИЗИТЫ ПО СЕКЦИЯМ СЕГОДНЯ:</b>\n"
            f"{visits_by_discipline_text}\n"
            f"🚨 <b>МЕНЕДЖМЕНТ (Проверить админа):</b>\n"
            f"❌ Ноль на балансе: <code>{stats.expired} чел.</code>\n"
            f"💤 Спящие (&gt;14 дней): <code>{stats.sleeping} чел.</code>\n\n"
            f"<i>⏱ Обновлено в {now.strftime('%H:%M:%S')}</i>"
        )

        # 10. 🌟 ФИКС: Тянем железную дату подписки SaaS из БД для отображения в клавиатуре
        # Обновляем инлайн-экран отчета с правильным порядком аргументов и датой подписки
        await callback.message.edit_text(
            text=report_text,
            reply_markup=admin_keyboard(
                club.id,
                club_settings,
                club.subscription_expire_at,
                staff_permissions=permissions_for_staff(staff) if staff else None
            ),
            parse_mode="HTML"
        )

    except Exception as e:
        await session.rollback()
        logger.error(f"❌ Ошибка ручного сохранения атлета по тарифу: {e}")
        await callback.message.answer("❌ Ошибка при сохранении в базу данных.")

    await callback.answer()


# ШАГ 6: Ловим кнопку подтверждения ручной оплаты наличными
@router.callback_query(F.data.startswith("confirm_manual_pay_"))
async def confirm_manual_pay(callback: types.CallbackQuery, club: Club):
    parts = callback.data.split("_")
    student_id = parts[3]
    price = parts[4]

    await callback.message.edit_text(
        f"✅ <b>Платеж успешно проведен!</b>\n\n"
        f"💵 Сумма <b>{price} ₽</b> получена наличными.\n"
        f"Карточка атлета ID <code>{student_id}</code> полностью активирована в системе клуба <b>{club.name}</b>.",
        parse_mode="HTML"
    )
    await callback.answer("Оплата внесена ✔")


@router.callback_query(F.data == "admin_cash_search")
async def cash_search_start(
        callback: types.CallbackQuery,
        state: FSMContext,
        is_owner: bool,  # <--- ИСПРАВИЛ (из Middleware)
        is_super_admin: bool  # <--- ИСПРАВИЛ (из Middleware)
):
    # Проверка прав (SaaS стиль)
    if not (is_owner or is_super_admin):
        return await callback.answer("❌ У вас нет прав доступа.", show_alert=True)

    await state.set_state(AdminManualAdd.waiting_for_search)

    # Добавим кнопку отмены
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Отмена", callback_data="admin")

    await callback.message.answer(
        "🔍 <b>Поиск атлета (Наличные)</b>\n\n"
        "Введите имя или фамилию для поиска по базе <b>вашего клуба</b>:",
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )
    await callback.answer()


@router.message(AdminManualAdd.waiting_for_search)
async def cash_search_results(
        message: types.Message,
        state: FSMContext,
        session: AsyncSession,
        club: Club  # <--- ИСПРАВИЛ (берем объект целиком из Middleware)
):
    # 1. Валидация и подготовка запроса
    search_text = message.text.strip()
    if len(search_text) < 2:
        return await message.answer("⚠️ Введите хотя бы 2 буквы для поиска.")

    search_query = f"%{search_text}%"

    try:
        # 2. 🛡️ ИЗОЛЯЦИЯ: Поиск строго внутри club.id
        stmt = (
            select(Student)
            .where(
                Student.name.ilike(search_query),
                Student.club_id == club.id  # <--- Используем ID из объекта
            )
            .order_by(Student.name)
        )

        result = await session.execute(stmt)
        results = result.scalars().all()

        if not results:
            # Даем кнопку отмены, чтобы не застрять
            kb = InlineKeyboardBuilder()
            kb.button(text="❌ В меню", callback_data="admin")
            return await message.answer(
                f"❌ По запросу «{search_text}» в вашем клубе никого не найдено.",
                reply_markup=kb.as_markup()
            )

        # 3. Собираем клавиатуру результатов
        builder = InlineKeyboardBuilder()
        for s in results:
            # Статус (активен/нет) для удобства админа
            status = "✅" if s.expire_date and s.expire_date > datetime.now() else "❌"
            builder.row(types.InlineKeyboardButton(
                text=f"{status} {s.name}",
                callback_data=f"cash_pay_{s.id}")
            )

        builder.row(types.InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin"))

        await message.answer(
            f"🔍 Найдено атлетов в клубе <b>{club.name}</b>: {len(results)}",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )

        # Стейт очищаем, поиск завершен успешно
        await state.clear()

    except Exception as e:
        logger.error(f"❌ Ошибка поиска в клубе {club.id}: {e}")
        await message.answer("⚠️ Произошла ошибка при обращении к базе данных.")


@router.callback_query(F.data == "admin_manual_visit")
async def start_manual_visit_search(callback: types.CallbackQuery, state: FSMContext):
    # Без этой строки следующий хендлер (который ты скинул) не увидит твой текст
    await state.set_state(AdminManualAdd.waiting_for_search_visit)
    await callback.message.answer("🔍 Введите имя или фамилию атлета для поиска:")
    await callback.answer()


@router.message(AdminManualAdd.waiting_for_search_visit)
async def manual_visit_results(
        message: types.Message,
        state: FSMContext,
        session: AsyncSession,
        club: Club  # <--- ИСПРАВИЛ (берем объект целиком из Middleware)
):
    search_query = f"%{message.text.strip()}%"
    try:
        # 🛡️ ИЗОЛЯЦИЯ: Поиск строго внутри club.id
        stmt = (
            select(Student)
            .where(
                Student.name.ilike(search_query),
                Student.club_id == club.id  # <--- Используем ID из объекта
            )
            .order_by(Student.name)
            .limit(20)  # Разумный лимит для мобильного экрана
        )

        result = await session.execute(stmt)
        results = result.scalars().all()

        if not results:
            return await message.answer(
                f"❌ В базе клуба <b>{club.name}</b> никто не найден.",
                parse_mode="HTML"
            )

        builder = InlineKeyboardBuilder()
        now = datetime.now()

        for s in results:
            # Проверяем активность абонемента
            is_active = s.expire_date and s.expire_date > now
            status = "🟢" if is_active else "🔴"

            # В callback_data зашиваем ID студента для хендлера списания занятия
            builder.row(types.InlineKeyboardButton(
                text=f"{status} {s.name}",
                callback_data=f"admin_manual_checkin_{s.id}")
            )

        builder.row(types.InlineKeyboardButton(text="⬅️ Отмена", callback_data="admin"))

        await message.answer(
            f"🔎 Найдено в <b>{club.name}</b>: {len(results)} чел.\n"
            f"Выберите атлета для <b>отметки о входе</b>:",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )

        # Состояние не сбрасываем, чтобы админ мог поискать другого,
        # если этот список ему не подошел. Сбросишь в хендлере чекина.

    except Exception as e:
        logger.error(f"❌ Ошибка поиска визита (Клуб {club.id}): {e}")
        await message.answer("⚠️ Ошибка при поиске в базе данных.")



@router.callback_query(F.data.startswith("admin_manual_checkin_"))
async def process_manual_checkin(
        callback: types.CallbackQuery,
        state: FSMContext,
        session: AsyncSession,
        club: Club,
        club_settings: dict
):
    # 1. Защита от двойного клика в интерфейсе ТГ
    if any(word in (callback.message.text or "") for word in
           ["✅ ВХОД ОТМЕЧЕН", "🔴 ДОСТУП ЗАПРЕЩЕН", "Вход отмечен вручную"]):
        return await callback.answer("Этот запрос уже обработан! ⚠️", show_alert=True)

    student_id = int(callback.data.split("_")[-1])

    # 2. Передаем задачу нашему универсальному сервису прохода!
    res = await process_athlete_gate_pass(
        student_id, session, club_settings, expected_club_id=club.id
    )

@router.callback_query(F.data == "admin_public_links")
async def admin_public_links_start(callback: types.CallbackQuery, state: FSMContext, club_settings: dict):
    ui = club_settings.get("ui", {})
    await state.set_state(AdminSettingsSG.waiting_for_public_links)
    await callback.message.answer(
        "Введите одной строкой через |:\nsite_url | support_username | site_on (1/0) | support_on (1/0)\n\n"
        f"Текущие: {ui.get('site_url','')} | {ui.get('support_link','')} | {int(ui.get('site_enabled', False))} | {int(ui.get('support_enabled', True))}"
    )
    await callback.answer()

@router.callback_query(F.data.in_({"toggle_site_link", "toggle_support_link"}))
async def toggle_public_link(callback: types.CallbackQuery, club: Club, club_settings: dict, session: AsyncSession, redis: Redis):
    key = "site_enabled" if callback.data == "toggle_site_link" else "support_enabled"
    ui = club_settings.setdefault("ui", {}); ui[key] = not ui.get(key, key == "support_enabled")
    await session.execute(update(Club).where(Club.id == club.id).values(club_settings=club_settings)); await session.commit(); await redis.delete(f"club_config:{callback.bot.token}")
    await callback.answer("✅ Переключено")
    await admin_settings_menu(callback, club_settings, club.id)

@router.callback_query(F.data == "edit_site_url")
async def edit_site_url(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminSettingsSG.waiting_for_site_url); await callback.message.answer("Отправьте URL сайта (https://...):"); await callback.answer()

@router.callback_query(F.data == "edit_support_username")
async def edit_support_username(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminSettingsSG.waiting_for_support_username); await callback.message.answer("Отправьте Telegram username поддержки, например @admin:"); await callback.answer()

@router.message(AdminSettingsSG.waiting_for_site_url)
async def save_site_url(message: types.Message, state: FSMContext, club: Club, club_settings: dict, session: AsyncSession, redis: Redis):
    value = (message.text or "").strip()
    if not value.startswith(("https://", "http://")): return await message.answer("URL должен начинаться с https:// или http://")
    club_settings.setdefault("ui", {})["site_url"] = value
    await session.execute(update(Club).where(Club.id == club.id).values(club_settings=club_settings)); await session.commit(); await redis.delete(f"club_config:{message.bot.token}"); await state.clear(); await message.answer("✅ Сайт сохранён")

@router.message(AdminSettingsSG.waiting_for_support_username)
async def save_support_username(message: types.Message, state: FSMContext, club: Club, club_settings: dict, session: AsyncSession, redis: Redis):
    value = (message.text or "").strip().lstrip("@")
    if not value or any(ch.isspace() for ch in value): return await message.answer("Введите корректный username Telegram")
    club_settings.setdefault("ui", {})["support_link"] = value
    await session.execute(update(Club).where(Club.id == club.id).values(club_settings=club_settings)); await session.commit(); await redis.delete(f"club_config:{message.bot.token}"); await state.clear(); await message.answer("✅ Username поддержки сохранён")

@router.message(AdminSettingsSG.waiting_for_public_links)
async def admin_public_links_save(message: types.Message, state: FSMContext, club: Club, club_settings: dict, session: AsyncSession, redis: Redis):
    parts = [x.strip() for x in (message.text or "").split("|")]
    if len(parts) != 4 or parts[2] not in {"0", "1"} or parts[3] not in {"0", "1"}:
        return await message.answer("Неверный формат. Нужно: сайт | @поддержка | 1/0 | 1/0")
    site, support = parts[0], parts[1].lstrip("@")
    if site and not site.startswith(("https://", "http://")): return await message.answer("Сайт должен начинаться с https:// или http://")
    club_settings.setdefault("ui", {}).update({"site_url": site, "support_link": support, "site_enabled": parts[2] == "1", "support_enabled": parts[3] == "1"})
    await session.execute(update(Club).where(Club.id == club.id).values(club_settings=club_settings)); await session.commit(); await redis.delete(f"club_config:{message.bot.token}")
    await state.clear(); await message.answer("✅ Сайт и поддержка сохранены")

    if not res["success"]:
        # Если абонемент кончился или ошибка — красиво выводим админу
        await callback.message.edit_text(
            f"🔴 <b>ДОСТУП ЗАПРЕЩЕН</b>\nℹ️ {res['message']}",
            parse_mode="HTML"
        )
        await state.clear()
        return await callback.answer("Ошибка прохода! ❌", show_alert=True)

    # 3. SaaS-УВЕДОМЛЕНИЕ ДЛЯ КЛИЕНТА (РОДИТЕЛЯ) — шлем только если сессия новая
    if not res["is_inside_session"] and res["parent_id"]:
        try:
            await callback.bot.send_message(
                chat_id=int(res["parent_id"]),
                text=f"🔔 <b>Вход зафиксирован администратором:</b> {res['student_name']}\n📊 {res['message']}\nПриятной тренировки! 💪",
                parse_mode="HTML"
            )
        except Exception as parent_err:
            logger.warning(f"Не удалось уведомить родителя через ручной чекин: {parent_err}")

    # 4. Обновляем интерфейс самому админу в боте (убираем инлайн кнопки)
    freeze_notice = f"\n❄️ <b>Досрочная разморозка!</b> Сдвиг на <b>-{res['returned_early_days']} дн.</b>" if res[
                                                                                                                  "is_was_frozen"] and \
                                                                                                              res[
                                                                                                                  "returned_early_days"] > 0 else ""

    await callback.message.edit_text(
        f"🟢 <b>Вход отмечен вручную</b>\n👤 Атлет: <b>{res['student_name']}</b>\n"
        f"📊 {res['message']}\n📅 Действует до: <b>{res['expire_str']}</b>"
        f"{freeze_notice}\n{res['turnstile_status']}",
        parse_mode="HTML"
    )

    await callback.answer("Посещение зафиксировано")
    await state.clear()


@router.callback_query(F.data == "staff_manage")
async def staff_manage(callback: types.CallbackQuery, club: Club, session: AsyncSession, is_owner: bool, is_super_admin: bool):
    if not (is_owner or is_super_admin):
        return await callback.answer("Доступ запрещён: персоналом управляет владелец клуба.", show_alert=True)
    staff = (await session.execute(select(ClubStaff).where(ClubStaff.club_id == club.id).order_by(ClubStaff.id))).scalars().all()
    text = "👔 <b>Персонал клуба</b>\n\n" + ("\n".join(f"• <code>{x.telegram_id}</code> — {x.full_name or 'без имени'} — <b>{x.role}</b> — {'✅' if x.is_active else '❌'}" for x in staff) or "Сотрудников пока нет.")
    kb = InlineKeyboardBuilder()
    for item in staff:
        kb.button(text=f"🗑 Удалить {item.telegram_id}", callback_data=f"staff_delete_{item.id}")
    kb.button(text="➕ Добавить сотрудника", callback_data="staff_add")
    kb.button(text="⬅️ Назад", callback_data="admin")
    kb.adjust(1)
    await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("staff_delete_"))
async def staff_delete(callback: types.CallbackQuery, club: Club, session: AsyncSession, is_owner: bool, is_super_admin: bool):
    if not (is_owner or is_super_admin):
        return await callback.answer("Доступ запрещён", show_alert=True)
    staff_id = int(callback.data.rsplit("_", 1)[1])
    staff = await session.get(ClubStaff, staff_id)
    if not staff or (not is_super_admin and staff.club_id != club.id):
        return await callback.answer("Сотрудник не найден", show_alert=True)
    await session.delete(staff)
    await session.commit()
    await staff_manage(callback, club=club, session=session, is_owner=is_owner, is_super_admin=is_super_admin)


@router.callback_query(F.data == "staff_add")
async def staff_add_start(callback: types.CallbackQuery, state: FSMContext, is_owner: bool, is_super_admin: bool):
    if not (is_owner or is_super_admin):
        return await callback.answer("Доступ запрещён", show_alert=True)
    await state.set_state(AdminStates.waiting_for_staff_telegram_id)
    await callback.message.answer("Введите Telegram ID сотрудника:")
    await callback.answer()


@router.message(AdminStates.waiting_for_staff_telegram_id)
async def staff_add_id(message: types.Message, state: FSMContext):
    if not (message.text or "").strip().isdigit():
        return await message.answer("ID должен состоять только из цифр.")
    await state.update_data(staff_telegram_id=int(message.text.strip()))
    await state.set_state(AdminStates.waiting_for_staff_role)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="☕ Бариста", callback_data="staff_role_cashier")],
        [InlineKeyboardButton(text="🥋 Тренер", callback_data="staff_role_coach")],
        [InlineKeyboardButton(text="📋 Менеджер", callback_data="staff_role_manager")],
    ])
    await message.answer("Выберите роль сотрудника:", reply_markup=kb)


@router.callback_query(AdminStates.waiting_for_staff_role, F.data.startswith("staff_role_"))
async def staff_add_role(callback: types.CallbackQuery, state: FSMContext, club: Club, session: AsyncSession, is_owner: bool, is_super_admin: bool):
    if not (is_owner or is_super_admin):
        await state.clear(); return await callback.answer("Доступ запрещён", show_alert=True)
    role = callback.data.removeprefix("staff_role_")
    data = await state.get_data()
    existing = (await session.execute(select(ClubStaff).where(ClubStaff.club_id == club.id, ClubStaff.telegram_id == data["staff_telegram_id"]))).scalar_one_or_none()
    if existing:
        existing.role = role; existing.is_active = True
    else:
        session.add(ClubStaff(club_id=club.id, telegram_id=data["staff_telegram_id"], role=role, full_name=callback.from_user.full_name))
    await session.commit(); await state.clear()
    await callback.message.answer(f"✅ Сотрудник добавлен. Роль: {role}")
    await callback.answer()


@router.callback_query(F.data == 'admin_edit_payments')
async def edit_payments_info(callback: types.CallbackQuery, state: FSMContext):
    # Используем edit_text вместо answer, чтобы не плодить сообщения
    await callback.message.edit_text(
        "📝 <b>Редактирование реквизитов</b>\n\n"
        "Введите новый текст.\n"
        "Например: <code>+79001234567 (Иван И.)</code>",
        parse_mode="HTML"
    )
    await state.set_state(AdminSettings.waiting_for_payment_info)
    await callback.answer()


@router.message(AdminSettings.waiting_for_payment_info)
async def save_payment_info(message: types.Message, state: FSMContext, session, club: Club, redis):
    new_info = message.text.strip()

    # 1. Глубокое копирование словаря, чтобы изменить объект
    new_settings = dict(club.club_settings)
    if 'ui' not in new_settings:
        new_settings['ui'] = {}
    new_settings['ui']["payment_info"] = new_info

    try:
        # 2. ЯВНЫЙ UPDATE (бьем прямой наводкой в БД по ID клуба)
        await session.execute(
            update(Club)
            .where(Club.id == club.id)
            .values(club_settings=new_settings)
        )
        await session.commit()

        # 3. УДАЛЯЕМ КЭШ (чтобы мидлварь в следующий раз снова пошла в БД)
        cache_key = f"club_config:{message.bot.token}"
        await redis.delete(cache_key)

        logger.warning(f"!!! БАЗА ОБНОВЛЕНА ДЛЯ КЛУБА {club.id} !!!")
        await message.answer(f"✅ Готово! Новые реквизиты записаны в БД.")
        await state.clear()

    except Exception as e:
        logger.error(f"ОШИБКА ЗАПИСИ: {e}")
        await session.rollback()
        await message.answer("❌ Ошибка при сохранении.")


# ИСПРАВЛЕНО: F.data вместо F.dara
@router.callback_query(F.data == "admin_turnstile_main")
async def admin_turnstile_main(callback: types.CallbackQuery, club_settings: dict):
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
        builder.row(types.InlineKeyboardButton(text="🔄 Изменить настройки", callback_data="setup_t_start"))
        builder.row(types.InlineKeyboardButton(text="🛑 Выключить СКУД", callback_data="disable_t_confirm"))
        builder.row(types.InlineKeyboardButton(text="🛠 Назад в настройки", callback_data="admin_settings"))
        current_url = turnstile_config.get("base_url", "Не задан")

        # ИСПРАВЛЕНО: Исправлены теги </b> и добавлен правильный <code> для красивого копирования адреса
        await callback.message.edit_text(
            f"📡 <b>Интеграция СКУД (Турникет) активна</b>\n\n"
            f"📌 Текущий адрес реле: <code>{current_url}</code>\n\n"
            f"Вы можете изменить параметры или отключить интеграцию.",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )


# Заполнение данных
@router.callback_query(F.data == "setup_t_start")
async def setup_turnstile_url_step(callback: types.CallbackQuery, state: FSMContext):
    # ИСПРАВЛЕНО: Проверьте ваш класс TurnstileSetup, обычно пишется wait_for_url (через r)
    await state.set_state(TurnstileSetup.wait_for_url)
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🛠 Назад в настройки", callback_data="admin_settings"))

    # ИСПРАВЛЕНО: Закрыт тег </b>
    await callback.message.edit_text(
        "📝 <b>Шаг 1: Введите адрес KeenDNS (или IP)</b>\n\n"
        "⚠️ Протокол (http://) и порты указывать не нужно, бот подставит их сам.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


from aiogram import F  # Не забудьте импортировать F, если еще не сделали этого


# ИСПРАВЛЕНО: Стейт изменен на правильный wait_for_url
@router.message(TurnstileSetup.wait_for_url)
async def process_t_url(message: types.Message, state: FSMContext):
    url_input = message.text.strip().lower()

    # ИСПРАВЛЕНО: Проверяем, начинается ли ввод с http:// или https://, чтобы не ломать ссылки
    if not (url_input.startswith("http://") or url_input.startswith("https://")):
        url_input = f"http://{url_input}"

    await state.update_data(base_url=url_input)
    await state.set_state(TurnstileSetup.wait_for_password)

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="❌ Без пароля (Пропустить)", callback_data="skip_t_password"))

    # ИСПРАВЛЕНО: Закрыт тег </b>
    await message.answer(
        "🔐 <b>Шаг 2: Введите пароль от веб-панели реле</b>\n\n"
        "Если вы установили пароль на доступ к плате, то введите его сейчас в ответном сообщении.\n"
        "Если на плате остался стандартный доступ без пароля, нажмите на кнопку ниже:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


# НОВЫЙ ХЕНДЛЕР: Обработка нажатия на кнопку "Пропустить"
@router.callback_query(TurnstileSetup.wait_for_password, F.data == "skip_t_password")
async def skip_t_password(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, club: Club):
    user_data = await state.get_data()
    await state.clear()

    # Отвечаем на колбэк, чтобы кнопка перестала "часиками" крутиться
    await callback.answer()

    # Вызываем вашу функцию сохранения, передавая пустую строку в качестве пароля
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
            # ✅ ИСПРАВЛЕНО: Привязываем объект к сессии для выполнения UPDATE, а не INSERT
            db_club = await session.merge(club)

            # Явно говорим SQLAlchemy, что JSON-поле внутри привязанного объекта изменилось
            flag_modified(db_club, "settings")

            await session.commit()
            await callback.answer("🔒 Интеграция СКУД успешно отключена", show_alert=True)
        except Exception as e:
            logger.error(f"Ошибка при отключении СКУД в БД: {e}")
            await session.rollback()  # 🚨 Обязательно откатываем сессию при ошибке, чтобы СУБД не висла
            await callback.answer("❌ Не удалось сохранить изменения в БД", show_alert=True)
            return

        # Передаем обновленный словарь настроек в главное меню СКУД
        await admin_turnstile_main(callback, club_settings=current_settings)
    else:
        await callback.answer("СКУД и так не был настроен", show_alert=True)


# Тарифы и цены
async def save_club_settings(session, redis:Redis, bot_token: str, club_id: int, updated_settings: dict):
    """Обновляет JSON-поле настроек в СУБД и очищает Redis-кэш для middleware"""
    await session.execute(
        update(Club).where(Club.id == club_id).values(club_settings=updated_settings)
    )
    await session.commit()
    await redis.delete(f"club_config:{bot_token}")


async def return_to_tariff_menu(message: types.Message, club_settings: dict, disc_id: str):
    """Генерирует актуальное меню тарифов конкретной дисциплины после любых изменений в FSM"""
    discipline = club_settings.get("disciplines", {}).get(disc_id, {})
    tariffs = discipline.get("tariffs", [])
    d_type = discipline.get("type", "lessons")

    builder = InlineKeyboardBuilder()

    # ФИКС: Возвращаем тумблер типа секции, иначе после ввода цены он пропадал из меню!
    type_label = "Безлимитная (♾)" if d_type == "unlimited" else "По занятиям (🔢)"
    builder.row(
        types.InlineKeyboardButton(text=f"🔄 Тип секции: {type_label}", callback_data=f"adm_tar_toggle_{disc_id}")
    )

    # Генерируем кнопки тарифов по их строгому индексу idx
    for idx, tariff in enumerate(tariffs):
        if d_type == "unlimited":
            t_text = f"💳 {tariff.get('days')} дн. — {tariff.get('price')} руб."
        else:
            count = tariff.get("count", 0)
            count_label = "♾ Безлимит" if count == 999 else f"{count} зан."
            t_text = f"💳 {count_label} / {tariff.get('days')} дн. — {tariff.get('price')} руб."
        builder.row(types.InlineKeyboardButton(text=t_text, callback_data=f"adm_tar_edit_{disc_id}_{idx}"))

    builder.row(types.InlineKeyboardButton(text="➕ Добавить тариф", callback_data=f"adm_tar_add_{disc_id}"))
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_tariffs_sections"))

    # Жестко настраиваем сетку кнопок strictly по одной в ряд
    builder.adjust(1)

    await message.answer(
        text=f"🥋 <b>Направление: {discipline.get('name')}</b>\n"
             f"Текущий режим: <u>{'Безлимитные абонементы' if d_type == 'unlimited' else 'Списание занятий'}</u>\n\n"
             f"Управление существующей тарифной сеткой:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


#НАВИГАЦИЯ и Выбор секций

# ================= НАВИГАЦИЯ И ВЫБОР СЕКЦИЙ =================

@router.callback_query(F.data == "admin_tariffs_sections")
async def admin_tariffs_sections_list(callback: types.CallbackQuery, club_settings: dict):
    """Выводит список всех дисциплин, зарегистрированных в системе"""
    builder = InlineKeyboardBuilder()
    disciplines = club_settings.get("disciplines", {})

    for disc_id, disc_data in disciplines.items():
        name = disc_data.get("name", disc_id)
        d_type = "♾" if disc_data.get("type") == "unlimited" else "🔢"
        builder.row(types.InlineKeyboardButton(
            text=f"{d_type} {name}", callback_data=f"adm_tar_sect_{disc_id}"
        ))
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_settings"))
    await callback.message.edit_text(
        "<b>💰 Настройка тарифных планов клуба</b>\n\nВыберите интересующее направление тренировок:",
        reply_markup=builder.as_markup(), parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("adm_tar_sect_"))
async def admin_manage_section_tariffs(callback: types.CallbackQuery, club_settings: dict):
    """Меню управления тарифами конкретной секции с защитой от сломанных возвратов"""

    # ИСПРАВЛЕНО: Безопасный разбор строки. Забираем ID дисциплины по четкому индексу,
    # даже если в callback.data прилетел сложный префикс от кнопки возврата или удаления!
    parts = callback.data.split("_")
    if len(parts) < 4:
        return await callback.answer("❌ Ошибка формата данных секции!", show_alert=True)

    disc_id = parts[3]  # Строго 4-й элемент (например, 'boxing') после 'adm', 'tar', 'sect'

    discipline = club_settings.get("disciplines", {}).get(disc_id)
    if not discipline:
        return await callback.answer("❌ Указанная секция не найдена!", show_alert=True)

    builder = InlineKeyboardBuilder()
    d_type = discipline.get("type", "lessons")

    type_label = "Безлимитная (♾)" if d_type == "unlimited" else "По занятиям (🔢)"
    builder.row(
        types.InlineKeyboardButton(text=f"🔄 Тип секции: {type_label}", callback_data=f"adm_tar_toggle_{disc_id}")
    )

    tariffs = discipline.get("tariffs", [])

    # Генерируем кнопки существующих тарифов по их строгому индексу idx
    for idx, tariff in enumerate(tariffs):
        if d_type == "unlimited":
            t_text = f"💳 {tariff.get('days')} дн. — {tariff.get('price')} руб."
        else:
            count = tariff.get("count", 0)
            count_label = "♾ Безлимит" if count == 999 else f"{count} зан."
            t_text = f"💳 {count_label} / {tariff.get('days')} дн. — {tariff.get('price')} руб."

        builder.row(types.InlineKeyboardButton(text=t_text, callback_data=f"adm_tar_edit_{disc_id}_{idx}"))

    builder.row(types.InlineKeyboardButton(text="➕ Добавить тариф", callback_data=f"adm_tar_add_{disc_id}"))
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_tariffs_sections"))
    builder.adjust(1)  # Выстраиваем всё строго в один вертикальный ряд

    # Динамический текст подсказки
    if not tariffs:
        tariffs_info = "⚠️ <b>Ни одного тарифного плана еще не создано!</b>\nНажмите кнопку ниже, чтобы добавить первый тариф."
    else:
        tariffs_info = "Управление существующей тарифной сеткой:"

    await callback.message.edit_text(
        text=f"🥋 <b>Направление: {discipline.get('name')}</b>\n"
             f"Текущий режим: <u>{'Безлимитные абонементы' if d_type == 'unlimited' else 'Списание занятий'}</u>\n\n"
             f"{tariffs_info}",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


# БЛОК 3: ТУМБЛЕР ПЕРЕКЛЮЧЕНИЯ ТИПА СЕКЦИИ (БЕЗЛИМИТ / ЗАНЯТИЯ)
# =========================================================================
@router.callback_query(F.data.startswith("adm_tar_toggle_"))
async def admin_toggle_section_type(
        callback: types.CallbackQuery,
        club_settings: dict,
        session,
        redis: Redis,
        bot,
        club_id: int
):
    """Переключение режима секции (Списание занятий 🔢 <-> Безлимит ♾)"""
    parts = callback.data.split("_")
    if len(parts) < 4:
        return await callback.answer("❌ Ошибка формата данных тумблера!", show_alert=True)

    disc_id = parts[3]

    if disc_id in club_settings.get("disciplines", {}):
        cur = club_settings["disciplines"][disc_id].get("type", "lessons")
        new_type = "unlimited" if cur == "lessons" else "lessons"

        club_settings["disciplines"][disc_id]["type"] = new_type

        # Если переключили в безлимит — принудительно ставим маркер 999
        if new_type == "unlimited":
            for t in club_settings["disciplines"][disc_id].get("tariffs", []):
                t["count"] = 999
        else:
            for t in club_settings["disciplines"][disc_id].get("tariffs", []):
                if t.get("count") == 999:
                    t["count"] = 8

        await save_club_settings(session, redis, bot.token, club_id, club_settings)
        await callback.answer("Тип направления изменен! ✨")

        # 🔥 ИСПРАВЛЕНО: Безопасный обход заморозки Pydantic v2 через создание копии объекта
        new_callback = callback.model_copy(update={"data": f"adm_tar_sect_{disc_id}"})
        await admin_manage_section_tariffs(new_callback, club_settings)


@router.callback_query(F.data.startswith("adm_tar_edit_"))
async def admin_edit_tariff_menu(callback: types.CallbackQuery, club_settings: dict):
    """Экран изменения конкретного выбранного тарифа с защитой от сдвига индексов"""

    # ИСПРАВЛЕНО: Безопасный разбор динамической строки без риска поймать пустую строку ""
    parts = callback.data.split("_")
    if len(parts) < 5:
        return await callback.answer("❌ Ошибка формата данных меню тарифа!", show_alert=True)

    disc_id = parts[3]  # Строго 4-й элемент (например, 'boxing')
    tariff_idx = parts[4]    # Строго 5-й элемент (индекс тарифа, например, '0')

    try:
        tariff_idx_int = int(tariff_idx)
    except ValueError:
        return await callback.answer("❌ Некорректный индекс тарифа!", show_alert=True)

    discipline = club_settings.get("disciplines", {}).get(disc_id)
    if not discipline:
        return await callback.answer("❌ Секция не найдена в настройках!", show_alert=True)

    tariffs = discipline.get("tariffs", [])
    if not (0 <= tariff_idx_int < len(tariffs)):
        return await callback.answer("❌ Выбранный тариф больше не существует!", show_alert=True)

    tariff = tariffs[tariff_idx_int]

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text=f"💰 Цена: {tariff['price']} руб.", callback_data=f"input_tar_price_{disc_id}_{tariff_idx}"))
    builder.row(types.InlineKeyboardButton(text=f"⏳ Срок: {tariff['days']} дней", callback_data=f"input_tar_days_{disc_id}_{tariff_idx}"))
    if discipline.get("type") == "lessons":
        count_label = "♾ Безлимит" if tariff.get("count") == 999 else str(tariff.get("count", 0))
        builder.row(types.InlineKeyboardButton(text=f"🔢 Занятий: {count_label}", callback_data=f"input_tar_count_{disc_id}_{tariff_idx}"))

    # 🔥 ДОБАВЛЯЕМ СЮДА ЭТУ СТРОКУ (с большими пробелами для удобства)
    builder.row(types.InlineKeyboardButton( text = f"👶 Мин. возраст: {tariff.get('min_age', 0)} лет",
                                            callback_data = f"input_tar_min_age_{disc_id}_{tariff_idx}" ))

    builder.row(types.InlineKeyboardButton(text="❌ Удалить тариф", callback_data=f"adm_tar_del_{disc_id}_{tariff_idx}"))
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"adm_tar_sect_{disc_id}"))
    builder.adjust(1)  # Выстраиваем кнопки строго в один вертикальный ряд

    await callback.message.edit_text(
        text=f"⚙️ <b>Редактирование тарифа ({discipline.get('name')})</b>\n\n"
             f"Вы можете изменить отдельные параметры или полностью удалить тариф:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


# =========================================================================
# БЛОК 5: САМО ТОЧЕЧНОЕ УДАЛЕНИЕ ТАРИФА ИЗ БАЗЫ
# =========================================================================
@router.callback_query(F.data.startswith("adm_tar_del_"))
async def admin_delete_tariff(
        callback: types.CallbackQuery,
        club_settings: dict,
        session,
        redis: Redis,
        bot,
        club_id: int
):
    """Удаление конкретного тарифа по индексу с жесткой защитой"""
    parts = callback.data.split("_")
    if len(parts) < 5:
        await callback.answer("❌ Ошибка структуры данных кнопки удаления", show_alert=True)
        return

    disc_id = parts[3]
    tariff_idx = parts[4]

    try:
        tariff_idx_int = int(tariff_idx)
    except ValueError:
        await callback.answer("❌ Некорректный индекс тарифа", show_alert=True)
        return

    discipline_block = club_settings.get("disciplines", {}).get(disc_id, {})
    tariffs = discipline_block.get("tariffs", [])

    if 0 <= tariff_idx_int < len(tariffs):
        target_tariff = tariffs[tariff_idx_int]
        count = int(target_tariff.get("count", 0) or 0)
        days = int(target_tariff.get("days", 30) or 30)

        # Проверка активных спортсменов строго в этой дисциплине
        stmt = select(Student).where(
            Student.club_id == club_id,
            Student.discipline == disc_id,
            Student.balance_lessons == count,
            Student.expire_date > datetime.now()
        )
        result = await session.execute(stmt)
        active_students = result.scalars().all()

        # Also protect active tariffs whose lesson balance has already been
        # partially consumed. Their current balance no longer equals the
        # original tariff count, so use the confirmed payment order as the
        # durable purchase evidence as well.
        purchased_stmt = (
            select(Student)
            .join(PaymentOrder, PaymentOrder.student_id == Student.id)
            .where(
                Student.club_id == club_id,
                Student.discipline == disc_id,
                Student.expire_date > datetime.now(),
                PaymentOrder.club_id == club_id,
                PaymentOrder.discipline == disc_id,
                PaymentOrder.lesson_count == count,
                PaymentOrder.days_to_add == days,
                PaymentOrder.status.in_(("CONFIRMED", "SUCCEEDED", "PAID")),
            )
            .distinct()
        )
        purchased_result = await session.execute(purchased_stmt)
        purchased_students = purchased_result.scalars().all()
        known_ids = {student.id for student in active_students}
        active_students.extend(student for student in purchased_students if student.id not in known_ids)

        if active_students:
            names = ", ".join([s.name for s in active_students[:3]])
            if len(active_students) > 3:
                names += " и др."

            return await callback.message.answer(
                f"❌ <b>Невозможно удалить тариф!</b>\n\n"
                f"Этот тарифный план сейчас активирован у действующих атлетов секции:\n"
                f"👤 <code>{names}</code>\n\n"
                f"Сначала измените их баланс или дождитесь окончания абонементов.",
                parse_mode="HTML"
            )

        # Удаляем строго один тариф по его индексу из массива
        tariffs.pop(tariff_idx_int)

        # Перезаписываем чистый массив обратно в общую структуру настроек
        club_settings["disciplines"][disc_id]["tariffs"] = tariffs

        await save_club_settings(session, redis, bot.token, club_id, club_settings)
        await callback.answer("Тариф успешно удален! 👌")

    # 🔥 ИСПРАВЛЕНО: Безопасный обход заморозки Pydantic v2 через создание копии объекта
    new_callback = callback.model_copy(update={"data": f"adm_tar_sect_{disc_id}"})
    await admin_manage_section_tariffs(new_callback, club_settings)

# ================= РАБОТА С ТЕКСТОВЫМ ВВОДОМ ЧЕРЕЗ FSM =================

@router.callback_query(F.data.startswith("input_tar_"))
async def admin_start_tariff_edit(callback: types.CallbackQuery, state: FSMContext, club_id: int):
    """Инициализация процесса изменения конкретного поля тарифа"""
    parts = callback.data.split("_")
    await state.update_data(edit_type=parts[2], disc_id=parts[3], tariff_idx=int(parts[4]), club_id=club_id)

    if parts[2] == "price":
        await state.set_state(AdminTariffStates.waiting_for_price)
        await callback.message.answer("💰 Введите новую <b>стоимость</b> тарифа (целое число, например 4000):",
                                      parse_mode="HTML")
    elif parts[2] == "days":
        await state.set_state(AdminTariffStates.waiting_for_days)
        await callback.message.answer("⏳ Введите новое <b>количество дней</b> действия абонемента:", parse_mode="HTML")
    elif parts[2] == "count":
        await state.set_state(AdminTariffStates.waiting_for_count)
        text = ("🔢 <b>Введите новое количество занятий.</b>\n\n"
                "♾ Для безлимитного тарифа напишите <b>Безлимит</b>.")
        await callback.message.answer(text=text, parse_mode="HTML")

    # 🔥 ДОБАВЛЯЕМ НАШУ НОВУЮ ВЕТКУ ВОЗРАСТА СЮДА
    elif parts[2] == "min_age":
        await state.set_state(AdminTariffStates.waiting_for_min_age)
        await callback.message.answer(
            text="👶 <b>Возрастной ценз для тарифа:</b>\n\n"
                 "Введите <b>минимальный возраст</b> ребенка в годах (целое число, например: <code>8</code> для бокса).\n\n"
                 "<i>Введите <code>0</code>, если у этого тарифа нет ограничений по возрасту.</i>",
            parse_mode="HTML"
        )

    await callback.answer()


@router.message(AdminTariffStates.waiting_for_min_age) # 🔥 ДОБАВИЛИ ДЕКОРАТОР СЮДА
@router.message(AdminTariffStates.waiting_for_price)
@router.message(AdminTariffStates.waiting_for_days)
@router.message(AdminTariffStates.waiting_for_count)
async def admin_save_tariff_field(message: types.Message, state: FSMContext, club_settings: dict, session, redis: Redis,
                                  bot):
    """Валидация и сохранение измененного текстового поля"""
    raw_value = (message.text or "").strip().lower()
    if field == "count" and raw_value in {"безлимит", "безлимитный", "unlimited"}:
        val = 999
    elif not raw_value.isdigit():
        return await message.answer("❌ Ошибка ввода! Пожалуйста, отправьте корректное целое число.")
    else:
        val = int(raw_value)
    s_data = await state.get_data()
    disc_id, idx, field = s_data["disc_id"], s_data["tariff_idx"], s_data["edit_type"]

    # Автоматически запишет val по ключу "min_age" в JSONB!
    club_settings["disciplines"][disc_id]["tariffs"][idx][field] = val
    await save_club_settings(session, redis, bot.token, s_data["club_id"], club_settings)
    await state.clear()
    await return_to_tariff_menu(message, club_settings, disc_id)

#Создание Тарифов
# 1. Ловим нажатие на кнопку "➕ Добавить тариф"
@router.callback_query(F.data.startswith("adm_tar_add_"))
async def admin_start_add_tariff(callback: types.CallbackQuery, state: FSMContext, club_id: int, club_settings: dict):
    disc_id = callback.data.split("_")[-1]
    d_type = club_settings["disciplines"][disc_id].get("type", "lessons")

    await state.update_data(disc_id=disc_id, club_id=club_id, d_type=d_type)
    await state.set_state(AdminTariffStates.add_price)

    await callback.message.answer(
        "➕ <b>Создание нового тарифа</b>\n\n"
        "<b>Шаг 1 из 3:</b> Введите стоимость тарифа в рублях (только число, например: 4000):",
        parse_mode="HTML"
    )
    await callback.answer()


# 2. Ловим ввод ЦЕНЫ
@router.message(AdminTariffStates.add_price)
async def admin_add_tariff_price(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("❌ Стоимость должна быть целым числом! Попробуйте еще раз:")

    await state.update_data(new_price=int(message.text))
    await state.set_state(AdminTariffStates.add_days)

    await message.answer(
        "<b>Шаг 2 из 3:</b> Введите количество дней действия абонемента (например: 30):",
        parse_mode="HTML"
    )


# 3. Ловим ввод ДНЕЙ (Записываем 999 для безлимитных секций)
# 3. Ловим ввод ДНЕЙ
@router.message(AdminTariffStates.add_days)
async def admin_add_tariff_days(
        message: types.Message,
        state: FSMContext
):
    if not message.text.isdigit():
        return await message.answer("❌ Срок действия должен быть числом дней! Попробуйте еще раз:")

    days = int(message.text)
    s_data = await state.get_data()
    d_type = s_data["d_type"]

    # ИСПРАВЛЕНО: Для безлимита сохраняем данные в FSM и требуем ВОЗРАСТ
    if d_type == "unlimited":
        await state.update_data(new_days=days, new_count=999) # 999 ставим автоматически в памяти
        await state.set_state(AdminTariffStates.add_min_age)   # Меняем состояние на ввод возраста

        await message.answer(
            text="<b>Шаг 3 из 4 (Безлимит):</b> Введите <b>минимальный возраст</b> ребенка для этого тарифа (например: 4).\n\n"
                 "<i>Введите 0, если ограничений по возрасту у этого тарифа нет.</i>",
            parse_mode="HTML"
        )

    # ЕСЛИ СЕКЦИЯ ОБЫЧНАЯ — ПЕРЕХОДИМ К ВВОДУ ЗАНЯТИЙ (как и было)
    else:
        await state.update_data(new_days=days)
        await state.set_state(AdminTariffStates.add_count)

        await message.answer(
            text="<b>Шаг 3 из 4:</b> Введите лимит количества занятий для этого тарифа (например: 12).\n\n"
                 "♾ <b>Для безлимитного тарифа напишите «Безлимит».</b>",
            parse_mode="HTML"
        )


# 4. Ловим ввод ЗАНЯТИЙ (Только для обычных секций)
# 4. Ловим ввод ЗАНЯТИЙ (Только для обычных секций)
@router.message(AdminTariffStates.add_count)
async def admin_add_tariff_count(
        message: types.Message,
        state: FSMContext
):
    raw_count = (message.text or "").strip().lower()
    if raw_count in {"безлимит", "безлимитный", "unlimited"}:
        count = 999
    elif raw_count.isdigit():
        count = int(raw_count)
    else:
        return await message.answer("❌ Введите число занятий или напишите «Безлимит».")

    # ИСПРАВЛЕНО: Сохраняем count в память FSM и переключаем на ввод возраста
    await state.update_data(new_count=count)
    await state.set_state(AdminTariffStates.add_min_age)

    await message.answer(
        text="<b>Шаг 4 из 4:</b> Введите <b>минимальный возраст</b> ребенка для этого тарифа (например: 8).\n\n"
             "<i>Введите 0, если ограничений по возрасту у этого тарифа нет.</i>",
        parse_mode="HTML"
    )

# 5. 🔥 ФИНАЛ: Ловим ввод ВОЗРАСТА при создании, собираем тариф и пушим в БД
@router.message(AdminTariffStates.add_min_age)
async def admin_add_tariff_min_age_final(
        message: types.Message,
        state: FSMContext,
        club_settings: dict,
        session,
        redis: Redis,
        bot
):
    if not message.text.isdigit():
        return await message.answer("❌ Возраст должен быть целым числом года! Попробуйте еще раз:")

    min_age = int(message.text)
    s_data = await state.get_data()
    disc_id = s_data["disc_id"]
    club_id = s_data["club_id"]

    # Собираем полноценный тарифный план с новым полем возрастного ценза
    new_tariff = {
        "count": s_data["new_count"],
        "days": s_data["new_days"],
        "price": s_data["new_price"],
        "min_age": min_age  # 👈 Записали!
    }

    if "tariffs" not in club_settings["disciplines"][disc_id]:
        club_settings["disciplines"][disc_id]["tariffs"] = []

    # Добавляем созданный тариф в JSONB-конфиг
    club_settings["disciplines"][disc_id]["tariffs"].append(new_tariff)

    # Автоматически активируем секцию, раз в ней появился тариф
    club_settings["disciplines"][disc_id]["active"] = True

    # Сохраняем в базу данных Postgres и очищаем Redis-кэш мидлвари
    await save_club_settings(session, redis, bot.token, club_id, club_settings)
    await state.clear()

    await message.answer("✨ <b>Новый тариф успешно создан и запущен!</b>", parse_mode="HTML")

    # Красиво возвращаем админа в меню тарифов секции
    await return_to_tariff_menu(message, club_settings, disc_id)


#SHEDULE SHEDULE
# =====================================================================
# ШАГ 1: ВЫБОР СЕКЦИИ (Сохраняем club_id, чтобы не терялся)
# =====================================================================
@router.callback_query(F.data == "admin_schedule_main")
async def admin_schedule_select_discipline(callback: types.CallbackQuery, state: FSMContext, club: Club, club_settings: dict):
    disciplines = club_settings.get("disciplines", {})
    
    if not disciplines:
        return await callback.answer("❌ В конфиге клуба пока нет созданных дисциплин!", show_alert=True)
        
    # Сохраняем ID клуба в стейт сразу, чтобы удаление понимало, где чистить базу
    await state.update_data(club_id=club.id)
    
    builder = InlineKeyboardBuilder()
    
    for disc_id, disc_data in disciplines.items():
        builder.row(types.InlineKeyboardButton(
            text=f"🥋 {disc_data['name']}",
            callback_data=f"adm_sch_manage_{disc_id}"
        ))
        
    builder.row(types.InlineKeyboardButton(text="🔙 Назад в админку", callback_data="admin"))
    
    await callback.message.edit_text(
        text="📅 <b>Управление расписанием</b>\n\nВыберите спортивную дисциплину для настройки сетки занятий:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()


# =====================================================================
# ШАГ 1.5: ВЫБОР ДНЯ НЕДЕЛИ
# =====================================================================
@router.callback_query(F.data.startswith("adm_sch_manage_"))
async def admin_start_schedule_manage(callback: types.CallbackQuery, state: FSMContext, club_id: int, club_settings: dict):
    disc_id = callback.data.split("_")[-1]
    
    await state.update_data(disc_id=disc_id, club_id=club_id)
    await state.set_state(AdminScheduleStates.choose_day)
    
    days_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗓 Пн", callback_data="sch_day_mon"), InlineKeyboardButton(text="🗓 Вт", callback_data="sch_day_tue")],
        [InlineKeyboardButton(text="🗓 Ср", callback_data="sch_day_wed"), InlineKeyboardButton(text="🗓 Чт", callback_data="sch_day_thu")],
        [InlineKeyboardButton(text="🗓 Пт", callback_data="sch_day_fri"), InlineKeyboardButton(text="🗓 Сб", callback_data="sch_day_sat")],
        [InlineKeyboardButton(text="🎉 Вс", callback_data="sch_day_sun")],
        [InlineKeyboardButton(text="⬅️ Назад в меню секции", callback_data=f"section_{disc_id}")]
    ])

    
    disc_name = club_settings["disciplines"][disc_id]["name"]
    await callback.message.edit_text(
        text=f"📅 <b>Управление расписанием: {disc_name}</b>\n\nВыберите день недели, чтобы посмотреть текущие занятия или добавить новое:",
        reply_markup=days_kb,
        parse_mode="HTML"
    )
    await callback.answer()


from aiogram.filters import StateFilter


@router.callback_query(F.data.startswith("sch_day_"), StateFilter("*"))
async def admin_schedule_choose_day(
        callback: types.CallbackQuery,
        state: FSMContext,
        club_settings: dict,
        manual_day: str = None  # Принимаем день напрямую при удалении
):
    # Принудительно возвращаем админу рабочее состояние для этого экрана
    await state.set_state(AdminScheduleStates.choose_day)

    # Если день передан вручную, берем его. Если нет — парсим из callback.data
    day = manual_day if manual_day else callback.data.split("_")[-1]

    s_data = await state.get_data()
    disc_id = s_data.get("disc_id")

    if not disc_id:
        return await callback.answer("Ошибка контекста: выберите секцию заново ❌", show_alert=True)

    await state.update_data(chosen_day=day)

    day_names = {"mon": "Понедельник", "tue": "Вторник", "wed": "Среда", "thu": "Четверг", "fri": "Пятница",
                 "sat": "Суббота", "sun": "Воскресенье"}

    discipline_block = club_settings["disciplines"].get(disc_id, {})
    schedule_data = discipline_block.get("schedule", {})

    if not isinstance(schedule_data, dict) or day not in schedule_data:
        day_lessons = []
    else:
        day_lessons = schedule_data[day]

    builder = InlineKeyboardBuilder()
    text_lines = [f"📅 <b>Расписание на {day_names[day]}</b>\n"]

    if not day_lessons:
        text_lines.append("<i>Занятий пока нет.</i>")
    else:
        for idx, lesson in enumerate(day_lessons):
            text_lines.append(
                f"#{idx + 1} | ⏱ <b>{lesson['time']}</b> — {lesson['coach']} (👥 Мест: {lesson['max_slots']})")
            builder.row(types.InlineKeyboardButton(
                text=f"❌ Удалить #{idx + 1} ({lesson['time']})",
                callback_data=f"adm_sch_del_{disc_id}_{day}_{idx}"
            ))

    builder.row(types.InlineKeyboardButton(text="➕ Добавить занятие", callback_data="adm_sch_start_input_time"))
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад к дням", callback_data=f"adm_sch_manage_{disc_id}"))

    await callback.message.edit_text(
        text="\n".join(text_lines),
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()


# =====================================================================
# ХЕНДЛЕР УДАЛЕНИЯ (Исправленный: answer() вызывается сразу + фоновое сохранение)
# ====================================================================

# ХЕНДЛЕР УДАЛЕНИЯ (Идеально последовательный, без конфликта сессий)
# =====================================================================
@router.callback_query(F.data.startswith("adm_sch_del_"))
async def admin_delete_schedule_lesson(
        callback: types.CallbackQuery,
        state: FSMContext,
        club_settings: dict,
        session,
        redis: Redis,
        bot
):
    # Отвечаем Телеграму сразу
    await callback.answer("Удалено!")

    _, _, _, disc_id, day, lesson_idx = callback.data.split("_")
    lesson_idx = int(lesson_idx)
    s_data = await state.get_data()
    club_id = s_data.get("club_id")

    try:
        lessons_list = club_settings["disciplines"][disc_id]["schedule"][day]
        if 0 <= lesson_idx < len(lessons_list):
            lessons_list.pop(lesson_idx)

            # СНАЧАЛА перерисовываем интерфейс, передавая день в аргумент manual_day
            await admin_schedule_choose_day(callback, state, club_settings, manual_day=day)

            # И ТОЛЬКО ПОСЛЕ ЭТОГО сохраняем изменения в БД и Redis
            await save_club_settings(session, redis, bot.token, club_id, club_settings)
            logger.success(f"🗑 Изменения расписания успешно сохранены в БД (Клуб {club_id})")

        else:
            logger.warning("Занятие не найдено, возможно уже удалено.")
            await admin_schedule_choose_day(callback, state, club_settings, manual_day=day)

    except Exception as e:
        logger.error(f"Ошибка удаления расписания: {e}")
        # Аварийно вытаскиваем админа в меню дня, тоже используя аргумент manual_day
        await admin_schedule_choose_day(callback, state, club_settings, manual_day=day)


# ПЕРЕХОД К ВВОДУ ВРЕМЕНИ ДЛЯ НОВОГО ЗАНЯТИЯ
# =====================================================================
@router.callback_query(F.data == "adm_sch_start_input_time")
async def admin_schedule_trigger_time_input(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminScheduleStates.add_time)
    await callback.message.answer(
        "⏱ <b>Шаг 1 из 3: Введите время начала занятия</b>\n\n"
        "Отправьте текст в формате ЧЧ:ММ (например: <code>19:30</code>):",
        parse_mode="HTML"
    )
    await callback.answer()


# ==========================================
# ШАГ 3: Ловим ввод ВРЕМЕНИ и просим тренера
# ==========================================
@router.message(AdminScheduleStates.add_time)
async def admin_add_schedule_time(message: types.Message, state: FSMContext):
    time_text = message.text.strip()
    
    # Проверка формата ЧЧ:ММ
    if ":" not in time_text or len(time_text) != 5:
        return await message.answer("❌ Неверный формат! Введите время строго в формате ЧЧ:ММ (например, 18:00):")
        
    await state.update_data(new_time=time_text)
    await state.set_state(AdminScheduleStates.add_coach)
    
    await message.answer(
        "👤 <b>Шаг 2 из 3: Введите имя тренера или название группы</b>\n\n"
        "Например: <i>Омаров А.</i> или <i>Общая группа</i>:",
        parse_mode="HTML"
    )


# ==========================================
# ШАГ 4: Ловим ввод ТРЕНЕРА и просим места
# ==========================================
@router.message(AdminScheduleStates.add_coach)
async def admin_add_schedule_coach(message: types.Message, state: FSMContext):
    await state.update_data(new_coach=message.text.strip())
    await state.set_state(AdminScheduleStates.add_slots)
    
    await message.answer(
        "🔢 <b>Шаг 3 из 3: Укажите лимит свободных мест на занятие</b>\n\n"
        "Введите максимальное количество атлетов (только число, например: 15):",
        parse_mode="HTML"
    )


# ==========================================
# ШАГ 5: Финал! Ловим места и сохраняем в базу и Redis
# ==========================================
@router.message(AdminScheduleStates.add_slots)
async def admin_finalize_schedule(
    message: types.Message,
    state: FSMContext,
    club_settings: dict,
    session,
    redis: Redis,
    bot
):
    if not message.text.isdigit():
        return await message.answer("❌ Лимит мест должен быть целым числом! Попробуйте еще раз:")
        
    max_slots = int(message.text)
    s_data = await state.get_data()
    
    disc_id = s_data["disc_id"]
    club_id = s_data["club_id"]
    day = s_data["chosen_day"]
    time_text = s_data["new_time"]
    coach_text = s_data["new_coach"]
    
    new_lesson = {
        "time": time_text,
        "coach": coach_text,
        "max_slots": max_slots,
        "taken_slots": 0
    }
    
    # Защита: если там была старая строка, пересобираем в пустую структуру расписания
    discipline_block = club_settings["disciplines"][disc_id]
    if "schedule" not in discipline_block or isinstance(discipline_block["schedule"], str):
        club_settings["disciplines"][disc_id]["schedule"] = {
            "mon": [], "tue": [], "wed": [], "thu": [], "fri": [], "sat": [], "sun": []
        }
        
    # Добавляем новую тренировку в нужный день
    club_settings["disciplines"][disc_id]["schedule"][day].append(new_lesson)
    
    # Авто-сортировка по времени, чтобы в базе всё лежало по порядку (09:00, 12:00, 19:00...)
    club_settings["disciplines"][disc_id]["schedule"][day].sort(key=lambda x: x["time"])
    
    # Твоя родная функция сохранения в Postgres + автоматический пуш в Redis!
    await save_club_settings(session, redis, bot.token, club_id, club_settings)
    await state.clear()
    
    day_names = {"mon": "Понедельник", "tue": "Вторник", "wed": "Среда", "thu": "Четверг", "fri": "Пятница", "sat": "Суббота", "sun": "Воскресенье"}
    
    await message.answer(
        f"✅ <b>Занятие успешно добавлено в расписание!</b>\n\n"
        f"📅 День: <b>{day_names[day]}</b>\n"
        f"⏱ Время: <b>{time_text}</b>\n"
        f"👤 Инструктор: <b>{coach_text}</b>\n"
        f"🔢 Мест в группе: <b>{max_slots}</b>",
        parse_mode="HTML"
    )


#FSM FSM FSM Youkassa Youkassa
@router.callback_query(F.data == "admin_setup_yookassa")
async def start_yookassa_setup(callback: types.CallbackQuery, state: FSMContext):
    """Начало настройки: запрашиваем Shop ID"""
    await state.set_state(YooKassaSetupStates.waiting_for_shop_id)

    cancel_kb = InlineKeyboardBuilder()
    cancel_kb.row(types.InlineKeyboardButton(text="❌ Отмена", callback_data="admin_settings"))

    await callback.message.edit_text(
        "📥 <b>Настройка интеграции с ЮKassa</b>\n\n"
        "Введите ваш <b>Shop ID</b> (Идентификатор магазина).\n"
        "Вы можете найти его в личном кабинете ЮKassa вверху страницы (обычно состоит только из цифр).",
        reply_markup=cancel_kb.as_markup(),
        parse_mode="HTML"
    )


@router.message(YooKassaSetupStates.waiting_for_shop_id)
async def process_shop_id(message: types.Message, state: FSMContext):
    """Принимаем Shop ID и запрашиваем Secret Key"""
    shop_id = message.text.strip()

    if not shop_id.isdigit():
        return await message.answer("⚠️ Ошибка! Shop ID должен состоять только из цифр. Попробуйте еще раз:")

    await state.update_data(shop_id=shop_id)
    await state.set_state(YooKassaSetupStates.waiting_for_secret_key)

    await message.answer(
        "🔑 Теперь введите ваш <b>Секретный ключ</b> (Секрет).\n\n"
        "Его можно сгенерировать в ЛК ЮKassa в разделе <i>«Интеграция» -> «Ключи API»</i>.\n"
        "Он начинается на <code>test_...</code> (для тестового режима) или <code>live_...</code> (для боевого).",
        parse_mode="HTML"
    )


@router.message(YooKassaSetupStates.waiting_for_secret_key)
async def process_secret_key(
        message: types.Message,
        state: FSMContext,
        session: AsyncSession,
        club_id: int
):
    """Принимаем Secret Key и сохраняем всё в JSONB поле базы данных"""
    secret_key = message.text.strip()

    if not (secret_key.startswith("test_") or secret_key.startswith("live_")):
        return await message.answer(
            "⚠️ Ошибка! Неверный формат ключа. Он должен начинаться с <code>test_</code> или <code>live_</code>.\n"
            "Попробуйте ввести ключ заново:",
            parse_mode="HTML"
        )

    user_data = await state.get_data()
    shop_id = user_data["shop_id"]

    await state.clear()

    # 💾 Используем with_for_update() для безопасной мутации JSONB настроек
    result = await session.execute(
        select(Club)
        .where(Club.id == club_id)
        .with_for_update()
    )
    club = result.scalar_one_or_none()

    if club:
        # Для 100% надежности в асинхронной среде сделаем копию словаря
        current_settings = copy.deepcopy(club.club_settings) if club.club_settings else {}

        if "payments" not in current_settings:
            current_settings["payments"] = {}

        # Записываем данные в JSONB структуру
        current_settings["payments"]["provider"] = "yookassa"
        current_settings["payments"]["yookassa_shop_id"] = shop_id
        current_settings["payments"]["yookassa_secret_key"] = secret_key

        if "features" not in current_settings:
            current_settings["features"] = {}
        current_settings["features"]["online_payments"] = True

        # Присваиваем обновленный словарь обратно модели
        club.club_settings = current_settings
        
        # ⚡ Принудительно взводим флаг изменений для Алхимии, чтобы апдейт улетел в БД
        flag_modified(club, "club_settings")

        # Теперь Postgres на Аэзе железно применит UPDATE
        await session.commit()

        back_kb = InlineKeyboardBuilder()
        back_kb.row(types.InlineKeyboardButton(text="⚙️ Вернуться в настройки", callback_data="admin_settings"))

        await message.answer(
            "✅ <b>Интеграция успешно настроена!</b>\n\n"
            f"<b>Shop ID:</b> <code>{shop_id}</code>\n"
            f"<b>Ключ:</b> <code>{secret_key[:8]}...****</code>\n\n"
            "Теперь ваши клиенты смогут привязывать карты и оплачивать подписки онлайн.",
            reply_markup=back_kb.as_markup(),
            parse_mode="HTML"
        )
    else:
        await message.answer("🚨 Произошла критическая ошибка: Клуб не найден в базе данных.")


@router.callback_query(F.data == "manage_club_limits")
async def manage_club_limits_handler(callback: types.CallbackQuery, club: Club):
    """Экран управления лимитами клуба"""
    club_settings = club.club_settings or {}
    limits = club_settings.get("limits", {})

    # Достаем текущие значения из JSONB или берем наши дефолты
    timeout = limits.get("session_timeout_minutes", 150)
    freeze_step = limits.get("freeze_days_step", 7)
    freeze_price = limits.get("freeze_price_per_day", 0)

    text = f"⚙️ <b>Управление лимитами клуба «{club.name}»</b>\n\n" \
           f"⏱ <b>Сессия визита (СКУД):</b> <code>{timeout} мин.</code> ({timeout / 60:.1f} ч.)\n" \
           f"<i>В течение этого времени повторные проходы через турникет не списывают занятия.</i>\n\n" \
           f"❄️ <b>Шаг заморозки абонемента:</b> <code>{freeze_step} дн.</code>\n" \
           f"<i>Минимальный пакет дней, на который списывается заморозка.</i>\n\n" \
           f"💳 <b>Платная заморозка:</b> <code>{freeze_price} ₽/день</code>\n" \
           f"<i>0 — покупка заморозки отключена.</i>\n\n" \
           f"Выберите параметр для изменения:"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱ Изменить время сессии", callback_data="change_limit_session")],
        [InlineKeyboardButton(text="❄️ Изменить шаг заморозки", callback_data="change_limit_freeze")],
        [InlineKeyboardButton(text="💳 Цена 1 дня заморозки", callback_data="change_freeze_price")],
        [InlineKeyboardButton(text="⬅️ Назад в настройки", callback_data="admin_settings")]
    ])

    await callback.message.edit_text(text=text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


# === ИЗМЕНЕНИЕ СЕССИИ ВИЗИТА ===
@router.callback_query(F.data == "change_limit_session")
async def change_limit_session(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminSettingsSG.waiting_for_session_timeout)
    await callback.message.answer("⏱ <b>Введите новое время сессии визита в минутах</b> (например, 120 для 2 часов):",
                                  parse_mode="HTML")
    await callback.answer()


# ==========================================
# ⏱ ИСПРАВЛЕННЫЙ ХЕНДЛЕР СЕССИИ ВИЗИТА (Строка 1808)
# ==========================================
@router.message(AdminSettingsSG.waiting_for_session_timeout)
async def process_session_timeout(
        message: types.Message,
        state: FSMContext,
        session: AsyncSession,
        club: Club,
        redis: Redis
):
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

        # Полный сброс кэша в Redis для этого токена бота
        await redis.delete(f"club_config:{message.bot.token}")
    except Exception as e:
        logger.error(f"Ошибка при сохранении таймаута СКУД: {e}")
        await session.rollback()
        return await message.answer("❌ Не удалось сохранить изменения лимитов в БД.")

    await state.clear()
    await message.answer(f"✅ <b>Время СКУД-сессии успешно изменено на {minutes} минут!</b>", parse_mode="HTML")


# === ИЗМЕНЕНИЕ ШАГА ЗАМОРОЗКИ ===
@router.callback_query(F.data == "change_limit_freeze")
async def change_limit_freeze(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminSettingsSG.waiting_for_freeze_step)
    await callback.message.answer("❄️ <b>Введите новый минимальный шаг заморозки в днях</b> (например, 7):",
                                  parse_mode="HTML")
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
async def toggle_webapp_loading(callback: types.CallbackQuery, club: Club, club_settings: dict,
                                session: AsyncSession, redis: Redis):
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
async def save_webapp_logo(message: types.Message, state: FSMContext, club: Club,
                           session: AsyncSession, redis: Redis):
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
        ui["logo_url"] = f"/static/uploads/logos/{filename}"
        loading = dict(ui.get("loading") or {})
        loading["enabled"] = True
        loading.setdefault("duration_ms", 1200)
        loading.setdefault("message", "Загружаем приложение…")
        ui["loading"] = loading
        # Загрузка логотипа должна сразу включать загрузочный экран.
        # Сохраняем ранее заданные длительность и текст, если они уже были.
        loading = dict(ui.get("loading") or {})
        loading["enabled"] = True
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
async def save_webapp_loading(message: types.Message, state: FSMContext, club: Club,
                              session: AsyncSession, redis: Redis):
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
    ui["logo_url"] = logo_url
    ui["loading"] = {"enabled": enabled, "duration_ms": duration, "message": text}
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
async def process_freeze_price(message: types.Message, state: FSMContext, club: Club, session: AsyncSession,
                               redis: Redis):
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


# ❄️ ИСПРАВЛЕННЫЙ ХЕНДЛЕР ШАГА ЗАМОРОЗКИ (Строка 1844)
# ==========================================
@router.message(AdminSettingsSG.waiting_for_freeze_step)
async def process_freeze_step(
        message: types.Message,
        state: FSMContext,
        session: AsyncSession,
        club: Club,
        redis: Redis
):
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

        # Полный сброс кэша в Redis для этого токена бота
        await redis.delete(f"club_config:{message.bot.token}")
    except Exception as e:
        logger.error(f"Ошибка при сохранении шага заморозки: {e}")
        await session.rollback()
        return await message.answer("❌ Не удалось сохранить изменения шага заморозки в БД.")

    await state.clear()
    await message.answer(f"✅ <b>Минимальный шаг заморозки успешно изменен на {days} дней!</b>", parse_mode="HTML")
