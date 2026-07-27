from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import time, datetime
from sqlalchemy import func
from services.gate_control import process_athlete_gate_pass
from handlers.skud import save_and_test_turnstile
from handlers.states import AdminStates, AdminSettingsSG
from redis.asyncio import Redis
import pandas as pd
import os
from handlers.buttons import get_scanner_keyboard
from aiogram.types import FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database.db import get_all_users_count, get_total_athletes_count, get_active_subs_count, User, get_daily_stats, Student, \
    Club, PaymentOrder, CartOrder, VisitLog, ClubStaff
from sqlalchemy import select
from handlers.buttons import admin_keyboard
from handlers.states import AdminManualAdd
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
import re
from aiogram import Router, F, types
import asyncio
from loguru import logger
from services.input_normalization import normalize_ru_phone, parse_user_date
from services.staff_permissions import permissions_for_staff
from services.audit import audit_event
from handlers.admin_students import router as admin_students_router
from handlers.admin_settings_panel import (
    router as admin_settings_router,
    admin_settings_menu,
    manage_disciplines_menu,
    toggle_logic,
    admin_public_links_start,
    toggle_public_link,
    edit_site_url,
    edit_support_username,
    save_site_url,
    save_support_username,
    admin_public_links_save,
    show_daily_report,
)
from handlers.admin_payments_panel import router as admin_payments_router
from handlers.admin_tariffs_schedule import router as admin_tariffs_schedule_router
from handlers.admin_payment_info_panel import router as admin_payment_info_router
from handlers.manual_payment_review import router as manual_payment_review_router


router = Router()
router.include_router(admin_students_router)
router.include_router(admin_settings_router)
router.include_router(admin_payments_router)
router.include_router(admin_tariffs_schedule_router)
router.include_router(admin_payment_info_router)
router.include_router(manual_payment_review_router)

# URL-контракты WebApp для админского магазина/склада
ADMIN_PRODUCT_SALE_PATH = "/webapp/admin-product-sale"
ADMIN_PRODUCTS_PATH = "/webapp/admin-products"


def _actor_context_from_admin(flags: dict[str, bool], staff=None, actor_id: int | None = None) -> dict:
    if flags.get("is_super_admin"):
        role = "super_admin"
    elif flags.get("is_owner"):
        role = "owner"
    else:
        role = str(getattr(staff, "role", "")).strip().casefold() if staff else "staff"
    return {
        "actor_user_id": actor_id,
        "actor_role": role,
        "actor_name": getattr(staff, "full_name", None) if staff else None,
    }


def _manual_phone_key(value: str | None) -> str:
    return normalize_ru_phone(value) or ""




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
        is_super_admin: bool,
        staff=None
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
async def start_manual_visit_search(callback: types.CallbackQuery, state: FSMContext, is_owner: bool, is_super_admin: bool, staff):
    if not (is_owner or is_super_admin or (staff and "qr_checkin" in permissions_for_staff(staff))):
        return await callback.answer("Доступ запрещён", show_alert=True)
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
        club_settings: dict,
        redis: Redis,
        is_owner: bool,
        is_super_admin: bool,
        staff,
):
    # 1. Защита от двойного клика в интерфейсе ТГ
    if any(word in (callback.message.text or "") for word in
           ["✅ ВХОД ОТМЕЧЕН", "🔴 ДОСТУП ЗАПРЕЩЕН", "Вход отмечен вручную"]):
        return await callback.answer("Этот запрос уже обработан! ⚠️", show_alert=True)

    student_id = int(callback.data.split("_")[-1])

    # 2. Передаем задачу нашему универсальному сервису прохода!
    res = await process_athlete_gate_pass(
        student_id, session, club_settings, expected_club_id=club.id, redis=redis
    )
    audit_event(
        "manual_checkin",
        club_id=club.id,
        action="create",
        object_type="visit",
        object_id=student_id,
        location="bot/manual_checkin",
        **_actor_context_from_admin({"is_owner": is_owner, "is_super_admin": is_super_admin}, staff=staff, actor_id=callback.from_user.id),
        success=bool(res.get("success")),
        turnstile_status=res.get("turnstile_status"),
        message=res.get("message"),
    )

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
    deleted_staff = {"telegram_id": staff.telegram_id, "role": staff.role, "id": staff.id}
    await session.delete(staff)
    await session.commit()
    audit_event(
        "staff_deleted",
        club_id=club.id,
        action="delete",
        object_type="staff",
        object_id=deleted_staff["id"],
        location="bot/staff_manage",
        **_actor_context_from_admin({"is_owner": is_owner, "is_super_admin": is_super_admin}, actor_id=callback.from_user.id),
        deleted_telegram_id=deleted_staff["telegram_id"],
        deleted_role=deleted_staff["role"],
    )
    await staff_manage(callback, club=club, session=session, is_owner=is_owner, is_super_admin=is_super_admin)


@router.callback_query(F.data == "staff_add")
async def staff_add_start(callback: types.CallbackQuery, state: FSMContext, is_owner: bool, is_super_admin: bool):
    if not (is_owner or is_super_admin):
        return await callback.answer("Доступ запрещён", show_alert=True)
    await state.set_state(AdminStates.waiting_for_staff_telegram_id)
    await callback.message.answer("Введите Telegram ID сотрудника:")
    await callback.answer()
    audit_event(
        "staff_add_started",
        club_id=callback.message.chat.id if callback.message else None,
        action="create",
        object_type="staff",
        location="bot/staff_manage",
        **_actor_context_from_admin({"is_owner": is_owner, "is_super_admin": is_super_admin}, actor_id=callback.from_user.id),
    )


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
    existing_user = await session.get(User, data["staff_telegram_id"])
    staff_name = getattr(existing_user, "full_name", None) or getattr(callback.from_user, "full_name", None)
    existing = (await session.execute(select(ClubStaff).where(ClubStaff.club_id == club.id, ClubStaff.telegram_id == data["staff_telegram_id"]))).scalar_one_or_none()
    if existing:
        existing.role = role; existing.is_active = True
        if staff_name and not existing.full_name:
            existing.full_name = staff_name
    else:
        session.add(ClubStaff(club_id=club.id, telegram_id=data["staff_telegram_id"], role=role, full_name=staff_name))
    await session.commit(); await state.clear()
    await callback.message.answer(f"✅ Сотрудник добавлен. Роль: {role}")
    await callback.answer()
    audit_event(
        "staff_saved",
        club_id=club.id,
        action="create" if not existing else "update",
        object_type="staff",
        object_id=getattr(existing, "id", None) or data["staff_telegram_id"],
        location="bot/staff_manage",
        **_actor_context_from_admin({"is_owner": is_owner, "is_super_admin": is_super_admin}, actor_id=callback.from_user.id),
        staff_telegram_id=data["staff_telegram_id"],
        role=role,
    )




