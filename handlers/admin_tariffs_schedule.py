from __future__ import annotations

from datetime import datetime

from aiogram import Router, F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from redis.asyncio import Redis
from sqlalchemy import select, update

from database.db import Club, Student, PaymentOrder
from handlers.states import AdminTariffStates, AdminScheduleStates
from services.audit import audit_event
from services.staff_permissions import permissions_for_staff
from loguru import logger

router = Router()


async def save_club_settings(session, redis: Redis, bot_token: str, club_id: int, updated_settings: dict):
    await session.execute(update(Club).where(Club.id == club_id).values(club_settings=updated_settings))
    await session.commit()
    await redis.delete(f"club_config:{bot_token}")


async def return_to_tariff_menu(message: types.Message, club_settings: dict, disc_id: str):
    discipline = club_settings.get("disciplines", {}).get(disc_id, {})
    tariffs = discipline.get("tariffs", [])
    d_type = discipline.get("type", "lessons")

    builder = InlineKeyboardBuilder()
    type_label = "Безлимитная (♾)" if d_type == "unlimited" else "По занятиям (🔢)"
    builder.row(types.InlineKeyboardButton(text=f"🔄 Тип секции: {type_label}", callback_data=f"adm_tar_toggle_{disc_id}"))

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
    builder.adjust(1)

    await message.answer(
        text=f"🥋 <b>Направление: {discipline.get('name')}</b>\n"
             f"Текущий режим: <u>{'Безлимитные абонементы' if d_type == 'unlimited' else 'Списание занятий'}</u>\n\n"
             f"Управление существующей тарифной сеткой:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "admin_tariffs_sections")
async def admin_tariffs_sections_list(callback: types.CallbackQuery, club_settings: dict, is_owner: bool | None = None, is_super_admin: bool | None = None, staff=None):
    if not (is_owner or is_super_admin or (staff and "tariffs_manage" in permissions_for_staff(staff))):
        return await callback.answer("Доступ запрещён", show_alert=True)
    builder = InlineKeyboardBuilder()
    disciplines = club_settings.get("disciplines", {})

    for disc_id, disc_data in disciplines.items():
        name = disc_data.get("name", disc_id)
        d_type = "♾" if disc_data.get("type") == "unlimited" else "🔢"
        builder.row(types.InlineKeyboardButton(text=f"{d_type} {name}", callback_data=f"adm_tar_sect_{disc_id}"))
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_settings"))
    await callback.message.edit_text("<b>💰 Настройка тарифных планов клуба</b>\n\nВыберите интересующее направление тренировок:", reply_markup=builder.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith("adm_tar_sect_"))
async def admin_manage_section_tariffs(callback: types.CallbackQuery, club_settings: dict, is_owner: bool | None = None, is_super_admin: bool | None = None, staff=None):
    if not (is_owner or is_super_admin or (staff and "tariffs_manage" in permissions_for_staff(staff))):
        return await callback.answer("Доступ запрещён", show_alert=True)
    parts = callback.data.split("_")
    if len(parts) < 4:
        return await callback.answer("❌ Ошибка формата данных секции!", show_alert=True)
    disc_id = parts[3]

    discipline = club_settings.get("disciplines", {}).get(disc_id)
    if not discipline:
        return await callback.answer("❌ Указанная секция не найдена!", show_alert=True)

    builder = InlineKeyboardBuilder()
    d_type = discipline.get("type", "lessons")
    type_label = "Безлимитная (♾)" if d_type == "unlimited" else "По занятиям (🔢)"
    builder.row(types.InlineKeyboardButton(text=f"🔄 Тип секции: {type_label}", callback_data=f"adm_tar_toggle_{disc_id}"))

    tariffs = discipline.get("tariffs", [])
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
    builder.adjust(1)

    tariffs_info = "⚠️ <b>Ни одного тарифного плана еще не создано!</b>\nНажмите кнопку ниже, чтобы добавить первый тариф." if not tariffs else "Управление существующей тарифной сеткой:"
    await callback.message.edit_text(
        text=f"🥋 <b>Направление: {discipline.get('name')}</b>\n"
             f"Текущий режим: <u>{'Безлимитные абонементы' if d_type == 'unlimited' else 'Списание занятий'}</u>\n\n"
             f"{tariffs_info}",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("adm_tar_toggle_"))
async def admin_toggle_section_type(callback: types.CallbackQuery, club_settings: dict, session, redis: Redis, bot, club_id: int, is_owner: bool, is_super_admin: bool, staff):
    parts = callback.data.split("_")
    if len(parts) < 4:
        return await callback.answer("❌ Ошибка формата данных тумблера!", show_alert=True)
    disc_id = parts[3]

    if disc_id in club_settings.get("disciplines", {}):
        cur = club_settings["disciplines"][disc_id].get("type", "lessons")
        new_type = "unlimited" if cur == "lessons" else "lessons"
        club_settings["disciplines"][disc_id]["type"] = new_type
        if new_type == "unlimited":
            for t in club_settings["disciplines"][disc_id].get("tariffs", []):
                t["count"] = 999
        else:
            for t in club_settings["disciplines"][disc_id].get("tariffs", []):
                if t.get("count") == 999:
                    t["count"] = 8

        await save_club_settings(session, redis, bot.token, club_id, club_settings)
        audit_event(
            "tariff_section_type_toggled",
            club_id=club_id,
            action="toggle",
            object_type="discipline",
            object_id=disc_id,
            location="bot/tariffs",
            actor_user_id=callback.from_user.id,
            actor_role="super_admin" if is_super_admin else ("owner" if is_owner else (str(getattr(staff, "role", "")).strip().casefold() if staff else "staff")),
            actor_name=callback.from_user.full_name,
            new_type=new_type,
        )
        await callback.answer("Тип направления изменен! ✨")
        new_callback = callback.model_copy(update={"data": f"adm_tar_sect_{disc_id}"})
        await admin_manage_section_tariffs(new_callback, club_settings)


@router.callback_query(F.data.startswith("adm_tar_edit_"))
async def admin_edit_tariff_menu(callback: types.CallbackQuery, club_settings: dict, is_owner: bool | None = None, is_super_admin: bool | None = None, staff=None):
    if not (is_owner or is_super_admin or (staff and "tariffs_manage" in permissions_for_staff(staff))):
        return await callback.answer("Доступ запрещён", show_alert=True)
    parts = callback.data.split("_")
    if len(parts) < 5:
        return await callback.answer("❌ Ошибка формата данных меню тарифа!", show_alert=True)

    disc_id = parts[3]
    tariff_idx = parts[4]

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
    builder.row(types.InlineKeyboardButton(text=f"👶 Мин. возраст: {tariff.get('min_age', 0)} лет", callback_data=f"input_tar_min_age_{disc_id}_{tariff_idx}"))
    builder.row(types.InlineKeyboardButton(text="❌ Удалить тариф", callback_data=f"adm_tar_del_{disc_id}_{tariff_idx}"))
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"adm_tar_sect_{disc_id}"))
    builder.adjust(1)
    await callback.message.edit_text(
        text=f"⚙️ <b>Редактирование тарифа ({discipline.get('name')})</b>\n\n"
             f"Вы можете изменить отдельные параметры или полностью удалить тариф:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("adm_tar_del_"))
async def admin_delete_tariff(callback: types.CallbackQuery, club_settings: dict, session, redis: Redis, bot, club_id: int, is_owner: bool | None = None, is_super_admin: bool | None = None, staff=None):
    if not (is_owner or is_super_admin or (staff and "tariffs_manage" in permissions_for_staff(staff))):
        return await callback.answer("Доступ запрещён", show_alert=True)
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
        stmt = select(Student).where(Student.club_id == club_id, Student.discipline == disc_id, Student.balance_lessons == count, Student.expire_date > datetime.now())
        result = await session.execute(stmt)
        active_students = result.scalars().all()
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
        tariffs.pop(tariff_idx_int)
        club_settings["disciplines"][disc_id]["tariffs"] = tariffs
        await save_club_settings(session, redis, bot.token, club_id, club_settings)
        audit_event("tariff_deleted", club_id=club_id, action="delete", object_type="tariff", object_id=f"{disc_id}:{tariff_idx_int}", location="bot/tariffs", actor_user_id=callback.from_user.id, actor_role="super_admin" if is_super_admin else ("owner" if is_owner else (str(getattr(staff, "role", "")).strip().casefold() if staff else "staff")), actor_name=callback.from_user.full_name, discipline=disc_id, tariff=target_tariff)
        await callback.answer("Тариф успешно удален! 👌")
    new_callback = callback.model_copy(update={"data": f"adm_tar_sect_{disc_id}"})
    await admin_manage_section_tariffs(new_callback, club_settings)


@router.callback_query(F.data.startswith("input_tar_"))
async def admin_start_tariff_edit(callback: types.CallbackQuery, state: FSMContext, club_id: int):
    parts = callback.data.split("_")
    await state.update_data(edit_type=parts[2], disc_id=parts[3], tariff_idx=int(parts[4]), club_id=club_id)
    if parts[2] == "price":
        await state.set_state(AdminTariffStates.waiting_for_price)
        await callback.message.answer("💰 Введите новую <b>стоимость</b> тарифа (целое число, например 4000):", parse_mode="HTML")
    elif parts[2] == "days":
        await state.set_state(AdminTariffStates.waiting_for_days)
        await callback.message.answer("⏳ Введите новое <b>количество дней</b> действия абонемента:", parse_mode="HTML")
    elif parts[2] == "count":
        await state.set_state(AdminTariffStates.waiting_for_count)
        await callback.message.answer("🔢 <b>Введите новое количество занятий.</b>\n\n♾ Для безлимитного тарифа напишите <b>Безлимит</b>.", parse_mode="HTML")
    elif parts[2] == "min_age":
        await state.set_state(AdminTariffStates.waiting_for_min_age)
        await callback.message.answer("👶 <b>Возрастной ценз для тарифа:</b>\n\nВведите <b>минимальный возраст</b> ребенка в годах (целое число, например: <code>8</code> для бокса).\n\n<i>Введите <code>0</code>, если у этого тарифа нет ограничений по возрасту.</i>", parse_mode="HTML")
    await callback.answer()


@router.message(AdminTariffStates.waiting_for_min_age)
@router.message(AdminTariffStates.waiting_for_price)
@router.message(AdminTariffStates.waiting_for_days)
@router.message(AdminTariffStates.waiting_for_count)
async def admin_save_tariff_field(message: types.Message, state: FSMContext, club_settings: dict, session, redis: Redis, bot):
    s_data = await state.get_data()
    disc_id, idx, field = s_data["disc_id"], s_data["tariff_idx"], s_data["edit_type"]
    raw_value = (message.text or "").strip().lower()
    if field == "count" and raw_value in {"безлимит", "безлимитный", "unlimited"}:
        val = 999
    elif not raw_value.isdigit():
        return await message.answer("❌ Ошибка ввода! Пожалуйста, отправьте корректное целое число.")
    else:
        val = int(raw_value)
    club_settings["disciplines"][disc_id]["tariffs"][idx][field] = val
    await save_club_settings(session, redis, bot.token, s_data["club_id"], club_settings)
    await state.clear()
    await return_to_tariff_menu(message, club_settings, disc_id)


@router.callback_query(F.data.startswith("adm_tar_add_"))
async def admin_start_add_tariff(callback: types.CallbackQuery, state: FSMContext, club_id: int, club_settings: dict, is_owner: bool | None = None, is_super_admin: bool | None = None, staff=None):
    if not (is_owner or is_super_admin or (staff and "tariffs_manage" in permissions_for_staff(staff))):
        return await callback.answer("Доступ запрещён", show_alert=True)
    disc_id = callback.data.split("_")[-1]
    d_type = club_settings["disciplines"][disc_id].get("type", "lessons")
    await state.update_data(disc_id=disc_id, club_id=club_id, d_type=d_type)
    await state.set_state(AdminTariffStates.add_price)
    await callback.message.answer("➕ <b>Создание нового тарифа</b>\n\n<b>Шаг 1 из 3:</b> Введите стоимость тарифа в рублях (только число, например: 4000):", parse_mode="HTML")
    await callback.answer()


@router.message(AdminTariffStates.add_price)
async def admin_add_tariff_price(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("❌ Стоимость должна быть целым числом! Попробуйте еще раз:")
    await state.update_data(new_price=int(message.text))
    await state.set_state(AdminTariffStates.add_days)
    await message.answer("<b>Шаг 2 из 3:</b> Введите количество дней действия абонемента (например: 30):", parse_mode="HTML")


@router.message(AdminTariffStates.add_days)
async def admin_add_tariff_days(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("❌ Срок действия должен быть числом дней! Попробуйте еще раз:")
    days = int(message.text)
    s_data = await state.get_data()
    d_type = s_data["d_type"]
    if d_type == "unlimited":
        await state.update_data(new_days=days, new_count=999)
        await state.set_state(AdminTariffStates.add_min_age)
        await message.answer("<b>Шаг 3 из 4 (Безлимит):</b> Введите <b>минимальный возраст</b> ребенка для этого тарифа (например: 4).\n\n<i>Введите 0, если ограничений по возрасту у этого тарифа нет.</i>", parse_mode="HTML")
    else:
        await state.update_data(new_days=days)
        await state.set_state(AdminTariffStates.add_count)
        await message.answer("<b>Шаг 3 из 4:</b> Введите лимит количества занятий для этого тарифа (например: 12).\n\n♾ <b>Для безлимитного тарифа напишите «Безлимит».</b>", parse_mode="HTML")


@router.message(AdminTariffStates.add_count)
async def admin_add_tariff_count(message: types.Message, state: FSMContext):
    raw_count = (message.text or "").strip().lower()
    if raw_count in {"безлимит", "безлимитный", "unlimited"}:
        count = 999
    elif raw_count.isdigit():
        count = int(raw_count)
    else:
        return await message.answer("❌ Введите число занятий или напишите «Безлимит».")
    await state.update_data(new_count=count)
    await state.set_state(AdminTariffStates.add_min_age)
    await message.answer("<b>Шаг 4 из 4:</b> Введите <b>минимальный возраст</b> ребенка для этого тарифа (например: 8).\n\n<i>Введите 0, если ограничений по возрасту у этого тарифа нет.</i>", parse_mode="HTML")


@router.message(AdminTariffStates.add_min_age)
async def admin_add_tariff_min_age_final(message: types.Message, state: FSMContext, club_settings: dict, session, redis: Redis, bot):
    if not message.text.isdigit():
        return await message.answer("❌ Возраст должен быть целым числом года! Попробуйте еще раз:")
    min_age = int(message.text)
    s_data = await state.get_data()
    disc_id = s_data["disc_id"]
    club_id = s_data["club_id"]
    new_tariff = {"count": s_data["new_count"], "days": s_data["new_days"], "price": s_data["new_price"], "min_age": min_age}
    if "tariffs" not in club_settings["disciplines"][disc_id]:
        club_settings["disciplines"][disc_id]["tariffs"] = []
    club_settings["disciplines"][disc_id]["tariffs"].append(new_tariff)
    club_settings["disciplines"][disc_id]["active"] = True
    await save_club_settings(session, redis, bot.token, club_id, club_settings)
    audit_event("tariff_created", club_id=club_id, action="create", object_type="tariff", object_id=f"{disc_id}:{len(club_settings['disciplines'][disc_id].get('tariffs', [])) - 1}", location="bot/tariffs", actor_user_id=message.from_user.id, actor_role="staff", actor_name=message.from_user.full_name, discipline=disc_id, tariff=new_tariff)
    await state.clear()
    await message.answer("✨ <b>Новый тариф успешно создан и запущен!</b>", parse_mode="HTML")
    await return_to_tariff_menu(message, club_settings, disc_id)


@router.callback_query(F.data == "admin_schedule_main")
async def admin_schedule_select_discipline(callback: types.CallbackQuery, state: FSMContext, club: Club, club_settings: dict, is_owner: bool, is_super_admin: bool, staff):
    if not (is_owner or is_super_admin or (staff and "schedule_view" in permissions_for_staff(staff))):
        return await callback.answer("Доступ запрещён", show_alert=True)
    disciplines = club_settings.get("disciplines", {})
    if not disciplines:
        return await callback.answer("❌ В конфиге клуба пока нет созданных дисциплин!", show_alert=True)
    await state.update_data(club_id=club.id)
    builder = InlineKeyboardBuilder()
    for disc_id, disc_data in disciplines.items():
        builder.row(types.InlineKeyboardButton(text=f"🥋 {disc_data['name']}", callback_data=f"adm_sch_manage_{disc_id}"))
    builder.row(types.InlineKeyboardButton(text="🔙 Назад в админку", callback_data="admin"))
    await callback.message.edit_text("📅 <b>Управление расписанием</b>\n\nВыберите спортивную дисциплину для настройки сетки занятий:", reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("adm_sch_manage_"))
async def admin_start_schedule_manage(callback: types.CallbackQuery, state: FSMContext, club_id: int, club_settings: dict, is_owner: bool, is_super_admin: bool, staff):
    if not (is_owner or is_super_admin or (staff and "schedule_edit" in permissions_for_staff(staff))):
        return await callback.answer("Доступ запрещён", show_alert=True)
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
    await callback.message.edit_text(text=f"📅 <b>Управление расписанием: {disc_name}</b>\n\nВыберите день недели, чтобы посмотреть текущие занятия или добавить новое:", reply_markup=days_kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("sch_day_"), StateFilter("*"))
async def admin_schedule_choose_day(callback: types.CallbackQuery, state: FSMContext, club_settings: dict, manual_day: str = None, is_owner: bool | None = None, is_super_admin: bool | None = None, staff=None):
    if not (is_owner or is_super_admin or (staff and "schedule_edit" in permissions_for_staff(staff))):
        return await callback.answer("Доступ запрещён", show_alert=True)
    await state.set_state(AdminScheduleStates.choose_day)
    day = manual_day if manual_day else callback.data.split("_")[-1]
    s_data = await state.get_data()
    disc_id = s_data.get("disc_id")
    if not disc_id:
        return await callback.answer("Ошибка контекста: выберите секцию заново ❌", show_alert=True)
    await state.update_data(chosen_day=day)
    day_names = {"mon": "Понедельник", "tue": "Вторник", "wed": "Среда", "thu": "Четверг", "fri": "Пятница", "sat": "Суббота", "sun": "Воскресенье"}
    discipline_block = club_settings["disciplines"].get(disc_id, {})
    schedule_data = discipline_block.get("schedule", {})
    day_lessons = [] if not isinstance(schedule_data, dict) or day not in schedule_data else schedule_data[day]
    builder = InlineKeyboardBuilder()
    text_lines = [f"📅 <b>Расписание на {day_names[day]}</b>\n"]
    if not day_lessons:
        text_lines.append("<i>Занятий пока нет.</i>")
    else:
        for idx, lesson in enumerate(day_lessons):
            text_lines.append(f"#{idx + 1} | ⏱ <b>{lesson['time']}</b> — {lesson['coach']} (👥 Мест: {lesson['max_slots']})")
            builder.row(types.InlineKeyboardButton(text=f"❌ Удалить #{idx + 1} ({lesson['time']})", callback_data=f"adm_sch_del_{disc_id}_{day}_{idx}"))
    builder.row(types.InlineKeyboardButton(text="➕ Добавить занятие", callback_data="adm_sch_start_input_time"))
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад к дням", callback_data=f"adm_sch_manage_{disc_id}"))
    await callback.message.edit_text(text="\n".join(text_lines), reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("adm_sch_del_"))
async def admin_delete_schedule_lesson(callback: types.CallbackQuery, state: FSMContext, club_settings: dict, session, redis: Redis, bot, is_owner: bool | None = None, is_super_admin: bool | None = None, staff=None):
    if not (is_owner or is_super_admin or (staff and "schedule_edit" in permissions_for_staff(staff))):
        return await callback.answer("Доступ запрещён", show_alert=True)
    await callback.answer("Удалено!")
    _, _, _, disc_id, day, lesson_idx = callback.data.split("_")
    lesson_idx = int(lesson_idx)
    s_data = await state.get_data()
    club_id = s_data.get("club_id")
    try:
        lessons_list = club_settings["disciplines"][disc_id]["schedule"][day]
        if 0 <= lesson_idx < len(lessons_list):
            lessons_list.pop(lesson_idx)
            await admin_schedule_choose_day(callback, state, club_settings, manual_day=day)
            await save_club_settings(session, redis, bot.token, club_id, club_settings)
            audit_event("schedule_lesson_deleted", club_id=club_id, action="delete", object_type="schedule_lesson", object_id=f"{disc_id}:{day}:{lesson_idx}", location="bot/schedule", actor_user_id=callback.from_user.id, actor_role="staff", actor_name=callback.from_user.full_name, discipline=disc_id, day=day)
            logger.success(f"🗑 Изменения расписания успешно сохранены в БД (Клуб {club_id})")
        else:
            logger.warning("Занятие не найдено, возможно уже удалено.")
            await admin_schedule_choose_day(callback, state, club_settings, manual_day=day)
    except Exception as e:
        logger.error(f"Ошибка удаления расписания: {e}")
        await admin_schedule_choose_day(callback, state, club_settings, manual_day=day)


@router.callback_query(F.data == "adm_sch_start_input_time")
async def admin_schedule_trigger_time_input(callback: types.CallbackQuery, state: FSMContext, is_owner: bool, is_super_admin: bool, staff):
    if not (is_owner or is_super_admin or (staff and "schedule_edit" in permissions_for_staff(staff))):
        return await callback.answer("Доступ запрещён", show_alert=True)
    await state.set_state(AdminScheduleStates.add_time)
    await callback.message.answer("⏱ <b>Шаг 1 из 2: Введите время начала занятия</b>\n\nОтправьте текст в формате ЧЧ:ММ (например: <code>19:30</code>):", parse_mode="HTML")
    await callback.answer()


@router.message(AdminScheduleStates.add_time)
async def admin_add_schedule_time(message: types.Message, state: FSMContext):
    time_text = message.text.strip()
    if ":" not in time_text or len(time_text) != 5:
        return await message.answer("❌ Неверный формат! Введите время строго в формате ЧЧ:ММ (например, 18:00):")
    await state.update_data(new_time=time_text)
    await state.set_state(AdminScheduleStates.add_coach)
    await message.answer("ℹ️ <b>Шаг 2 из 2: Введите информацию о занятии</b>\n\nНапример: <i>спарринги</i>, <i>техника</i>, <i>общая группа</i>:", parse_mode="HTML")


@router.message(AdminScheduleStates.add_coach)
async def admin_add_schedule_coach(message: types.Message, state: FSMContext):
    await state.update_data(new_coach=message.text.strip())
    await state.set_state(AdminScheduleStates.add_slots)
    await message.answer("✅ Информация сохранена. Занятие будет добавлено без лимита мест.")


@router.message(AdminScheduleStates.add_slots)
async def admin_finalize_schedule(message: types.Message, state: FSMContext, club_settings: dict, session, redis: Redis, bot, is_owner: bool | None = None, is_super_admin: bool | None = None, staff=None):
    if not (is_owner or is_super_admin or (staff and "schedule_edit" in permissions_for_staff(staff))):
        return await message.answer("Доступ запрещён")
    max_slots = 0
    if (message.text or "").strip().isdigit():
        max_slots = int(message.text.strip())
    s_data = await state.get_data()
    disc_id = s_data["disc_id"]
    club_id = s_data["club_id"]
    day = s_data["chosen_day"]
    time_text = s_data["new_time"]
    coach_text = s_data["new_coach"]
    new_lesson = {"time": time_text, "coach": coach_text, "max_slots": max_slots, "taken_slots": 0}
    discipline_block = club_settings["disciplines"][disc_id]
    if "schedule" not in discipline_block or isinstance(discipline_block["schedule"], str):
        club_settings["disciplines"][disc_id]["schedule"] = {"mon": [], "tue": [], "wed": [], "thu": [], "fri": [], "sat": [], "sun": []}
    club_settings["disciplines"][disc_id]["schedule"][day].append(new_lesson)
    club_settings["disciplines"][disc_id]["schedule"][day].sort(key=lambda x: x["time"])
    await save_club_settings(session, redis, bot.token, club_id, club_settings)
    await state.clear()
    audit_event("schedule_lesson_created", club_id=club_id, action="create", object_type="schedule_lesson", object_id=f"{disc_id}:{day}:{time_text}", location="bot/schedule", actor_user_id=message.from_user.id, actor_role="staff", actor_name=message.from_user.full_name, discipline=disc_id, day=day, time=time_text, info=coach_text, max_slots=max_slots)
    day_names = {"mon": "Понедельник", "tue": "Вторник", "wed": "Среда", "thu": "Четверг", "fri": "Пятница", "sat": "Суббота", "sun": "Воскресенье"}
    await message.answer(f"✅ <b>Занятие успешно добавлено в расписание!</b>\n\n📅 День: <b>{day_names[day]}</b>\n⏱ Время: <b>{time_text}</b>\nℹ️ Информация: <b>{coach_text or 'не указана'}</b>\n🔢 Лимит мест: <b>{max_slots if max_slots else 'не задан'}</b>", parse_mode="HTML")
