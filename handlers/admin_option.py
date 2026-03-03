from sqlalchemy.ext.asyncio import AsyncSession
from handlers.states import AdminStates
import asyncio
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database.db import get_all_users_count, get_active_subs_count, AsyncSessionLocal, User, get_daily_stats, Student
from config import ADMIN_IDS
from sqlalchemy import select
from handlers.buttons import admin_keyboard, get_scanner_keyboard
from handlers.states import AdminManualAdd
from aiogram.filters import Command
import os
import pandas as pd
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile, InlineKeyboardButton
from datetime import timedelta
from aiogram import Router, F, types
from datetime import datetime
from loguru import logger


router = Router()


@router.message(Command('admin'), F.from_user.id.in_(ADMIN_IDS))
@router.callback_query(F.data == "admin", F.from_user.id.in_(ADMIN_IDS))
async def admin_panel(event: types.Message | types.CallbackQuery):
    if isinstance(event, types.CallbackQuery):
        message = event.message
        await event.answer()
        await message.delete()
    else:
        message = event
    admin = event.from_user
    logger.info(f"🔑 Админ {admin.full_name} (ID: {admin.id}) открыл панель управления")
    try:
        all_users = await get_all_users_count()
        active_subs = await get_active_subs_count()
        logger.debug(f"📊 Статистика выдана админу {admin.id}: {all_users} всего, {active_subs} активных")
        text = (
            "📈 <b>Панель администратора AE Maykop</b>\n\n"
            f"👥 Всего пользователей в базе: <code>{all_users}</code>\n"
            f"💳 Активных абонементов: <code>{active_subs}</code>\n\n"
            "Чего желаете, босс?"
        )
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=get_scanner_keyboard()
        )
        await message.answer(
            "Управление функциями:",
            reply_markup=admin_keyboard()
        )
    except Exception as e:
        logger.error(f"❌ Ошибка в админ-панели для {admin.id}: {e}")
        await message.answer("⚠️ Ошибка при загрузке статистики из базы данных.")


@router.message(Command("admin"), F.from_user.id.in_(ADMIN_IDS))
async def admin_panel(message: types.Message):
    await message.answer(
        "🛠 <b>Панель администратора клуба</b>\n\n"
        "Выберите действие для управления базой атлетов и рассылок:",
        reply_markup=admin_keyboard(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "admin_broadcast", F.from_user.id.in_(ADMIN_IDS))
async def start_broadcast(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_broadcast_text)
    await callback.message.answer("Введите текст или отправьте фото/видео для рассылки всем пользователям:")


@router.message(AdminStates.waiting_for_broadcast_text, F.from_user.id.in_(ADMIN_IDS))
async def perform_broadcast(message: types.Message, state: FSMContext):
    admin = message.from_user
    logger.warning(f"📢 Админ {admin.full_name} (ID: {admin.id}) запустил массовую рассылку!")
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(User.user_id))
            users_ids = result.scalars().all()
    except Exception as e:
        logger.error(f"❌ Ошибка БД при получении списка ID для рассылки: {e}")
        return await message.answer("Ошибка доступа к базе данных.")
    if not users_ids:
        await message.answer("База данных пуста!")
        await state.clear()
        return
    count_success = 0
    count_blocked = 0
    total = len(users_ids)
    await message.answer(f"🚀 Рассылка началась (всего: {total} чел.)...")
    for user_id in users_ids:
        try:
            await message.copy_to(chat_id=user_id, reply_markup=None)
            count_success += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            if "Forbidden" in str(e) or "chat not found" in str(e):
                count_blocked += 1
                logger.debug(f"🚫 Пользователь {user_id} заблокировал бота.")
            else:
                logger.error(f"❌ Ошибка отправки пользователю {user_id}: {e}")
    logger.success(f"🏁 Рассылка завершена! Успешно: {count_success}, Блоков: {count_blocked} из {total}")
    await message.answer(
        f"✅ <b>Рассылка завершена!</b>\n"
        f"📩 Доставлено: <code>{count_success}</code>\n"
        f"🚫 Заблокировали бота: <code>{count_blocked}</code>\n"
        f"📊 Всего в базе: <code>{total}</code>",
        parse_mode="HTML"
        )
    await state.clear()


@router.callback_query(F.data == 'export_db', F.from_user.id.in_(ADMIN_IDS))
async def export_database(callback: types.CallbackQuery, session: AsyncSession):
    file_path = f"export_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    await callback.answer("⏳ Собираю данные...")
    try:
        users_res = await session.execute(select(User))
        students_res = await session.execute(select(Student))
        users = users_res.scalars().all()
        students = students_res.scalars().all()
        users_data = [
            {"parent_id": u.user_id, "parent_name": u.full_name}
            for u in users
        ]
        students_data = [
            {
                "parent_id": s.parent_id,
                "child_name": s.name,
                "expire": s.expire_date.strftime('%d.%m.%Y') if s.expire_date else "Нет",
                "frozen": "Да" if s.is_frozen else "Нет",
                "balance": s.balance_lessons
            }
            for s in students
        ]
        df_students = pd.DataFrame(students_data)
        df_parents = pd.DataFrame(users_data)
        if df_students.empty:
            return await callback.message.answer("📭 В базе пока нет атлетов для экспорта.")
        if not df_parents.empty:
            df_full = pd.merge(df_students, df_parents, on='parent_id', how='left')
        else:
            df_full = df_students
        df_full.to_csv(file_path, index=False, encoding='utf-8-sig')
        await callback.message.answer_document(
            FSInputFile(file_path),
            caption=(f"📊 <b>Экспорт базы</b>\n"
                     f"👥 Всего атлетов: {len(df_students)}\n"
                     f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
        )
    except Exception as e:
        logger.error(f"❌ Ошибка экспорта: {e}")
        await callback.message.answer("❌ Ошибка при формировании CSV")
        await session.rollback()
    finally:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                logger.warning(f"Не удалось удалить временный файл: {e}")


@router.callback_query(F.data == 'daily_report', F.from_user.id.in_(ADMIN_IDS))
async def show_daily_report(callback: types.CallbackQuery):
    admin = callback.from_user
    logger.info(f"📊 Админ {admin.full_name} (ID: {admin.id}) запросил дневной отчет")
    try:
        visits, active = await get_daily_stats()
        logger.debug(f"📈 Статистика для админа {admin.id}: {visits} визитов, {active} активных")
        report_text = (
            f"📊 <b>ОТЧЕТ ЗА СЕГОДНЯ</b> ({datetime.now().strftime('%d.%m.%Y')})\n\n"
            f"👤 <b>Посещений:</b> <code>{visits}</code>\n"
            f"💎 <b>Всего активных карт:</b> <code>{active}</code>\n\n"
        )

        await callback.message.edit_text(
            text=report_text,
            reply_markup=admin_keyboard(),
            parse_mode="HTML")
    except Exception as e:
        if "message is not modified" in str(e):
            await callback.answer("Данные актуальны")
        else:
            logger.error(f"❌ Ошибка при формировании отчета для {admin.id}: {e}")
            await callback.answer("Ошибка при загрузке статистики", show_alert=True)

    await callback.answer()


@router.callback_query(F.data == 'admin_add_manual')
async def manual_add_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("📝 Введите <b>Имя и Фамилию</b> нового атлета:", parse_mode="HTML")
    await state.set_state(AdminManualAdd.waiting_for_name)
    await callback.answer()


@router.message(AdminManualAdd.waiting_for_name)
async def manual_add_process_name(message: types.Message, state: FSMContext):
    await state.update_data(student_name=message.text)  # Сохраняем имя в FSM
    await message.answer(
        f"👤 Атлет: <b>{message.text}</b>\n\n"
        "📱 Теперь введите <b>номер телефона родителя</b>.\n"
        "<i>По этому номеру родитель сможет войти в свой кабинет.</i>",
        parse_mode="HTML"
    )
    await state.set_state(AdminManualAdd.waiting_for_phone)


@router.message(AdminManualAdd.waiting_for_phone)
async def manual_add_process_phone(message: types.Message, state: FSMContext):
    phone = "".join(filter(str.isdigit, message.text))
    if len(phone) < 10:
        return await message.answer("❌ Номер слишком короткий. Попробуйте еще раз (например, 79991234567):")

    data = await state.get_data()
    student_name = data['student_name']

    try:
        async with AsyncSessionLocal() as session:
            new_student = Student(
                name=student_name,
                parent_phone=phone,
                parent_id=None,
                expire_date=None,
                balance_lessons=0,
                can_freeze=1,
                is_frozen=0
            )
            session.add(new_student)
            await session.commit()
            await session.refresh(new_student)
            student_id = new_student.id

        # Готовим кнопки
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="💵 Оплатил наличными", callback_data=f"confirm_cash_{student_id}"))
        builder.row(InlineKeyboardButton(text="🏠 В меню", callback_data="admin"))

        await message.answer(
            f"✅ Атлет <b>{student_name}</b> (ID: {student_id}) успешно добавлен!\n"
            f"📱 Привязан телефон: <code>{phone}</code>\n\n"
            f"Хотите сразу активировать абонемент?",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
        await state.clear()

    except Exception as e:
        logger.error(f"Ошибка при ручном добавлении: {e}")
        await message.answer("❌ Произошла ошибка при сохранении в базу.")
        await state.clear()


@router.callback_query(F.data == "admin_cash_search")
async def cash_search_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("🔍 Введите имя или фамилию для поиска:")
    await state.set_state(AdminManualAdd.waiting_for_search)
    await callback.answer()


@router.message(AdminManualAdd.waiting_for_search)
async def cash_search_results(message: types.Message, state: FSMContext, session: AsyncSession):
    search_query = f"%{message.text}%"
    try:
        stmt = select(Student).where(Student.name.ilike(search_query)).order_by(Student.name)
        result = await session.execute(stmt)
        results = result.scalars().all()
        if not results:
            return await message.answer(
                "❌ Никого не нашел. Попробуйте ввести имя точнее или напишите 'отмена'.",
                reply_markup=InlineKeyboardBuilder().row(
                    InlineKeyboardButton(text="❌ Отмена", callback_data="admin_main")
                ).as_markup()
            )
        builder = InlineKeyboardBuilder()
        for s in results:
            builder.row(InlineKeyboardButton(text=f"👤 {s.name}", callback_data=f"cash_pay_{s.id}"))
        builder.row(InlineKeyboardButton(text="⬅️ В админку", callback_data="admin_main"))
        await message.answer(f"🔍 Найдено атлетов: {len(results)}", reply_markup=builder.as_markup())
        await state.clear()
    except Exception as e:
        logger.error(f"Ошибка поиска: {e}")
        await message.answer("⚠ Ошибка при обращении к базе данных.")


@router.callback_query(F.data == "admin_manual_visit")
async def manual_visit_search(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("🔍 Введите имя атлета для отметки:")
    await state.set_state(AdminManualAdd.waiting_for_search_visit)
    await callback.answer()


@router.message(AdminManualAdd.waiting_for_search_visit)
async def manual_visit_results(message: types.Message, state: FSMContext, session: AsyncSession):
    search_query = f"%{message.text}%"
    try:
        stmt = select(Student).where(Student.name.ilike(search_query)).order_by(Student.name).limit(20)
        result = await session.execute(stmt)
        results = result.scalars().all()
        if not results:
            return await message.answer("❌ Никого не нашел. Попробуйте другое имя.")
        builder = InlineKeyboardBuilder()
        now = datetime.now()
        for s in results:
            is_active = s.expire_date and s.expire_date > now
            status = "🟢" if is_active else "🔴"
            builder.row(InlineKeyboardButton(
                text=f"{status} {s.name}",
                callback_data=f"admin_manual_checkin_{s.id}")
            )
        builder.row(InlineKeyboardButton(text="⬅️ Отмена", callback_data="admin_main"))
        await message.answer(
            f"🔍 Найдено атлетов: {len(results)}\nКого отметить?",
            reply_markup=builder.as_markup()
        )
        await state.clear()
    except Exception as e:
        logger.error(f"❌ Ошибка поиска для отметки: {e}")
        await message.answer("Произошла ошибка при поиске. Попробуйте еще раз.")


@router.callback_query(F.data.startswith("admin_manual_checkin_"))
async def process_manual_checkin(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    student_id = int(callback.data.split("_")[-1])
    now = datetime.now()
    msg_unfreeze = ""
    try:
        student = await session.get(Student, student_id)
        if not student:
            return await callback.answer("❌ Атлет не найден!", show_alert=True)
        parent_id = student.parent_id
        student_name = student.name
        if student.is_frozen == 1:
            last_v = student.last_visit or now
            days_actually_frozen = (now - last_v).days
            if days_actually_frozen < 5:
                days_to_subtract = 5 - days_actually_frozen
                if student.expire_date:
                    student.expire_date -= timedelta(days=days_to_subtract)
                msg_unfreeze = f"\n❄️ <b>Разморозка!</b> Срок скорректирован (-{days_to_subtract} дн.)"
            else:
                msg_unfreeze = f"\n❄️ <b>Абонемент разморожен!</b>"
            student.is_frozen = 0
        balance = student.balance_lessons or 0
        if balance >= 900:
            usage_info = "♾ Режим: <b>Безлимит</b>"
        elif balance > 0:
            student.balance_lessons -= 1
            usage_info = f"📉 Осталось занятий: <b>{student.balance_lessons}</b>"
        else:
            return await callback.message.answer(
                f"🔴 <b>ОШИБКА</b>\n👤 {student_name}\n❌ Занятия закончились!",
                parse_mode="HTML"
            )
        student.last_visit = now
        session.add(student)
        await session.commit()
        await callback.message.edit_text(
            f"✅ <b>Вход отмечен вручную</b>\n"
            f"👤 Атлет: <b>{student_name}</b>\n"
            f"{usage_info}"
            f"{msg_unfreeze}",
            parse_mode="HTML"
        )
        if parent_id and parent_id != 0:
            try:
                await callback.bot.send_message(
                    chat_id=int(parent_id),
                    text=f"🔔 <b>Вход зафиксирован:</b> {student_name}\n{usage_info}\nПриятной тренировки! 💪",
                    parse_mode="HTML"
                )
            except Exception:
                pass
        await callback.answer("Посещение зафиксировано")
        await state.clear()
    except Exception as e:
        await session.rollback()
        logger.error(f"❌ Ошибка ручной отметки: {e}")
        await callback.answer("Ошибка при сохранении данных", show_alert=True)
