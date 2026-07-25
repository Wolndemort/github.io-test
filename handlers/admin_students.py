from __future__ import annotations

from datetime import datetime, timedelta

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import Club, Student, User
from handlers.states import AdminManualAdd
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
        builder.row(types.InlineKeyboardButton(text=info.get("name", code), callback_data=f"admin_manual_disc_{code}"))
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
        builder.row(
            types.InlineKeyboardButton(
                text=f"{count} зан. / {tariff.get('days', 30)} дн. — {tariff.get('price', 0)} ₽",
                callback_data=f"admin_manual_tariff_{discipline}_{index}",
            )
        )
    builder.row(types.InlineKeyboardButton(text="Без абонемента", callback_data=f"admin_manual_no_sub_{discipline}"))
    await state.set_state(AdminManualAdd.waiting_for_tariff)
    await callback.message.edit_text("Выберите тариф или вариант без абонемента:", reply_markup=builder.as_markup())
    await callback.answer()


async def _finish_manual_add(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    club: Club,
    discipline: str,
    tariff_idx: int | None,
):
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
    duplicate = next(
        (
            student
            for student in students
            if student.name.strip().casefold() == name.casefold()
            and student.birthday == birthday
            and (student.discipline or "").casefold() == discipline.casefold()
            and _manual_phone_key(student.parent_phone) == _manual_phone_key(phone)
        ),
        None,
    )
    if duplicate:
        await state.clear()
        await callback.message.answer("⚠️ Такой атлет уже есть в базе клуба. Новая запись не создана.")
        return await callback.answer()

    session.add(
        Student(
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
        )
    )
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
async def manual_add_without_subscription(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    club: Club,
):
    discipline = callback.data.removeprefix("admin_manual_no_sub_")
    await _finish_manual_add(callback, state, session, club, discipline, None)


@router.callback_query(F.data == "admin_quick_athletes")
async def admin_quick_athletes(
    callback: types.CallbackQuery,
    session: AsyncSession,
    club: Club,
    is_owner: bool,
    is_super_admin: bool,
    staff,
):
    if not (is_owner or is_super_admin or (staff and "athletes_view" in permissions_for_staff(staff))):
        return await callback.answer("Доступ запрещён", show_alert=True)
    students = (
        await session.execute(
            select(Student).where(Student.club_id == club.id).order_by(Student.name)
        )
    ).scalars().all()
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
    chunks = [text[i : i + 3800] for i in range(0, len(text), 3800)]
    await callback.answer()
    for chunk in chunks:
        await callback.message.answer(chunk, parse_mode="HTML")
