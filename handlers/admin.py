import asyncio
import qrcode
from aiogram.utils.keyboard import InlineKeyboardBuilder
import io

from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_all_users_count, get_active_subs_count, AsyncSessionLocal, User, get_daily_stats, \
    get_student_list, get_all_subscriptions, Student, process_student_freeze
from config import ADMIN_IDS, secret_key
from sqlalchemy import select
from handlers.buttons import get_bjj_keyboard, get_kids_keyboard, get_main_menu_keyboard, admin_keyboard, \
    get_scanner_keyboard, get_profile_keyboard, discipline
from handlers.states import AdminManualAdd
from database.db import add_abon
from aiogram.filters import Command
import os
import pandas as pd
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import FSInputFile, InlineKeyboardButton, BufferedInputFile, ReplyKeyboardMarkup, KeyboardButton
from datetime import timedelta
from aiogram import Router, F, types
from datetime import datetime
from loguru import logger
import hashlib
import hmac
from handlers.states import PaymentStates, RegistrationStates


def generate_signature(user_id, time_salt):
    msg = f"{user_id}:{time_salt}".encode()
    signature = hmac.new(
        secret_key.encode(),
        msg,
        hashlib.sha256
    ).hexdigest()
    return signature[:10]


router = Router()


class AdminStates(StatesGroup):
    waiting_for_broadcast_text = State()


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


@router.callback_query(F.data == 'begin')
async def go_to_begin(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_name = callback.from_user.first_name
    await callback.message.edit_text(
        f"<b>С возвращением, {user_name}!</b>\n\nЧем я могу вам помочь сегодня?",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data.in_(['profile', 'check_status_now']))
async def universal_profile_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    now = datetime.now()
    logger.debug(f"🔍 Запрос профиля для ID: {user_id}")
    try:
        students = await get_all_subscriptions(user_id)
        is_auth = bool(students)
        if not is_auth:
            status_text = (
                "⚠️ <b>У вас еще нет привязанных атлетов.</b>\n\n"
                "Если вы уже занимаетесь у нас, нажмите <b>«Привязать профиль»</b>, "
                "чтобы войти по номеру телефона.\n"
                "Или добавьте нового атлета вручную."
            )
        else:
            status_text = "🆔 <b>Ваши профили:</b>\n"
            for s in students:
                if not s.expire_date:
                    status = "❌ <b>Не куплен</b>"
                elif s.is_frozen:
                    status = "❄️ <b>ЗАМОРОЖЕН</b>"
                elif s.expire_date > now:
                    status = f"✅ <b>Активен</b> до <code>{s.expire_date.strftime('%d.%m.%Y')}</code>"
                else:
                    status = f"🔴 <b>ИСТЕК</b> (<code>{s.expire_date.strftime('%d.%m.%Y')}</code>)"

                phone_info = f" [📞 {s.parent_phone}]" if s.parent_phone else " [📱 нет номера]"
                lessons = f" (Занятий: {s.balance_lessons})" if hasattr(s, 'balance_lessons') else ""
                status_text += f"\n• <b>{s.name}</b>: {status}{lessons}\n,{phone_info}"

        await callback.message.edit_text(
            text=f"👤 <b>Личный кабинет</b>\n\n{status_text}\n\n"
                 "<i>Используйте кнопки ниже для управления:</i>",
            reply_markup=get_profile_keyboard(is_authorized=is_auth),
            parse_mode="HTML"
        )
        logger.debug(f"✅ Профиль ID {user_id} отображен (Auth: {is_auth})")
    except Exception as e:
        if "message is not modified" in str(e):
            await callback.answer("Данные актуальны")
        else:
            logger.error(f"❌ Ошибка в профиле: {e}")
            await callback.answer("Ошибка связи с базой данных")
    finally:
        try:
            await callback.answer()
        except:
            pass


@router.callback_query(F. data == 'bjj')
async def bjj_info(callback: types.CallbackQuery):
    await callback.message.edit_text(
        text='🥋 <b>Бразильское джиу-джитсу (BJJ)</b>\n\nВыберите нужный раздел:',
        parse_mode='HTML',
        reply_markup=get_bjj_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == 'kids')
async def kids_menu(callback: types.CallbackQuery):
    await callback.message.edit_text(
        text='🤼‍♂️ <b>Джиу-джитсу дети (Детская борьба)</b>\n'
             '\nСекция для детей от 4 лет. Развиваем силу, гибкость и дисциплину!',
        parse_mode='HTML',
        reply_markup=get_kids_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == 'price_bjj')
async def bjj_price(callback: types.CallbackQuery):
    user = callback.from_user
    logger.info(f"💰 Юзер {user.full_name} (ID: {user.id}) открыл прайс-лист BJJ")
    text = (
        "💰 <b>Стоимость абонементов Джиу-джитсу:</b>\n\n"
        "• Первая тренировка бесплатно\n"
        "• Разовая тренировка — 700₽\n"
        "• Месяц (24 занятия) — 5000₽\n"
        "• Безлимит на год — 55 000₽"
    )
    try:
        await callback.message.edit_text(
            text=text,
            parse_mode='HTML',
            reply_markup=get_bjj_keyboard()
        )
    except Exception as e:
        if "message is not modified" not in str(e):
            logger.error(f"❌ Ошибка при показе прайса BJJ для {user.id}: {e}")

    await callback.answer()


@router.callback_query(F.data == 'schedule_bjj')
async def bjj_schedule(callback: types.CallbackQuery):
    text = (
        "🗓 <b>Расписание Джиу-джитсу(GI-в кимоно/noGI-без кимоно):</b>\n\n"
        "• Понедельник GI: 20:00\n"
        "• Вторник noGI: 20:00\n"
        "• Среда GI: 20:00\n"
        "• Четверг noGI: 20:00\n"
        "• Пятница noGI(День борьбы): 20:00\n"
    )
    await callback.message.edit_text(text=text, parse_mode='HTML', reply_markup=get_bjj_keyboard())
    await callback.answer()


@router.callback_query(F.data == 'price_kids')
async def kids_price(callback: types.CallbackQuery):
    user = callback.from_user
    logger.info(f"💰 Юзер {user.full_name} (ID: {user.id}) открыл прайс-лист KIDS")
    text = (
        "💰 <b>Стоимость детского абонемента:</b>\n\n"
        "• Пробная тренировка — БЕСПЛАТНО\n"
        "• Месяц занятий — 4000₽\n"
    )
    try:
        await callback.message.edit_text(
            text=text,
            parse_mode='HTML',
            reply_markup=get_kids_keyboard()
        )
    except Exception as e:
        if "message is not modified" not in str(e):
            logger.error(f"❌ Ошибка при показе прайса KIDS для {user.id}: {e}")

    await callback.answer()


@router.callback_query(F.data == 'schedule_kids')
async def kids_schedule(callback: types.CallbackQuery):
    text = (
        "🗓 <b>Расписание Джиу-Джитсу дети:\n\n</b>"
        "• Вторник: 17:00\n"
        "• Четверг: 17:00\n"
    )
    await callback.message.edit_text(text=text, parse_mode='HTML', reply_markup=get_kids_keyboard())
    await callback.answer()


@router.callback_query(F.data == 'choose_section')
async def show_sections(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "<b>Выберите направление тренировок:</b>",
        reply_markup=discipline(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith('buy_'))
async def buy_handler(callback: types.CallbackQuery, state: FSMContext):
    user = callback.from_user
    sport_type = callback.data.split('_')[1]
    students = await get_student_list(user.id)
    if not students:
        await callback.answer("Сначала добавьте атлета в профиле!", show_alert=True)
        return
    await state.update_data(sport_type=sport_type)
    builder = InlineKeyboardBuilder()
    for s in students:
        builder.row(InlineKeyboardButton(
            text=f"🥋 {s.name}",
            callback_data=f"student_pay_{s.id}")
        )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="begin"))
    await callback.message.edit_text(
        f"Вы выбрали направление: <b>{sport_type.upper()}\n</b>"
        "Теперь выберите, за кого вносится оплата:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith('student_pay_'))
async def request_receipt(callback: types.CallbackQuery, state: FSMContext):
    student_id = int(callback.data.split('_')[2])
    await state.update_data(chosen_student_id=student_id)
    await state.set_state(PaymentStates.waiting_for_receipt)
    await callback.message.edit_text(
        "💳 <b>Реквизиты для перевода:\n\n</b>"
        "Сумма: 5000₽/4000\n"
        "СПБ: `+79606666165` (Адам.О)\n"
        "Банк: Тинькофф\n\n"
        "⚠️<b> Важно: После оплаты пришлите **скриншот чека** ответным сообщением в этот чат.</b>",
        parse_mode="HTML")
    await callback.answer()


@router.message(PaymentStates.waiting_for_receipt, F.photo)
async def handle_receipt_submission(message: types.Message, state: FSMContext):
    data = await state.get_data()
    student_id = data.get("chosen_student_id")
    sport_type = data.get("sport_type", "не указан")
    async with AsyncSessionLocal() as session:
        student = await session.get(Student, student_id)
        student_name = student.name if student else "Неизвестный"
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Оформить", callback_data=f"adm_confirm_{student_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_decline_{message.from_user.id}"))
    await message.bot.send_photo(
        chat_id=ADMIN_IDS[0],
        photo=message.photo[-1].file_id,
        caption=(
            f"<b>💰 Новая оплата!</b>\n"
            f"👤 Отправитель: @{message.from_user.username or 'без юзернейма'}\n"
            f"🥋 За кого: {student_name} (ID: {student_id})\n"
            f"🥊 Направление: {sport_type.upper()}"
        ),
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await state.clear()
    await message.answer("✅ Чек отправлен тренеру на проверку. Я пришлю уведомление, когда абонемент будет продлен.")


@router.callback_query(F.data.startswith("adm_confirm_"))
async def admin_confirm_pay(callback: types.CallbackQuery):
    student_id = int(callback.data.split("_")[-1])
    result = await add_abon(student_id)
    if result:
        new_expire, parent_id = result
        await callback.message.edit_caption(
            caption=callback.message.caption + f"<b>\n\n✅ ОФОРМЛЕНО до {new_expire}</b>",
            parse_mode="HTML"
        )
        try:
            await callback.bot.send_message(
                chat_id=parent_id,
                text=f"<b>💳 Ваша оплата подтверждена!\nАбонемент продлен до: {new_expire}</b>",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление пользователю {parent_id}: {e}")
    else:
        await callback.answer("Ошибка: студент не найден в базе", show_alert=True)


@router.callback_query(F.data.startswith("adm_decline_"))
async def admin_decline_pay(callback: types.CallbackQuery):
    user_id = int(callback.data.split("_")[-1])
    await callback.message.edit_caption(caption=callback.message.caption + "<b>\n\n❌ ОТКЛОНЕНО</b>")
    await callback.bot.send_message(
        chat_id=user_id,
        text="❌ Ваш чек отклонен. Пожалуйста, проверьте данные или свяжитесь с тренером."
    )


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
async def export_database(callback: types.CallbackQuery):
    file_path = "club_database_full.csv"
    await callback.answer("⏳ Собираю данные из Postgres...")
    try:
        async with AsyncSessionLocal() as session:
            users_res = await session.execute(select(User))
            students_res = await session.execute(select(Student))
            users_data = [
                {"parent_id": u.user_id, "parent_name": u.full_name}
                for u in users_res.scalars().all()
            ]
            students_data = [
                {
                    "id": s.id,
                    "parent_id": s.parent_id,
                    "child_name": s.name,
                    "expire": s.expire_date,
                    "frozen": s.is_frozen,
                    "balance": s.balance_lessons
                }
                for s in students_res.scalars().all()
            ]
        df_parents = pd.DataFrame(users_data)
        df_students = pd.DataFrame(students_data)
        if not df_students.empty and not df_parents.empty:
            df_full = pd.merge(df_students, df_parents, on='parent_id', how='left')
        else:
            df_full = df_students
        df_full.to_csv(file_path, index=False, encoding='utf-8-sig')
        await callback.message.answer_document(
            FSInputFile(file_path),
            caption=f"📊 <b>Экспорт завершен</b>\nАтлетов: {len(df_students)}"
        )
    except Exception as e:
        logger.error(f"❌ Ошибка экспорта: {e}")
        await callback.message.answer("❌ Ошибка при формировании CSV")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


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


@router.callback_query(F.data.startswith("confirm_freeze_"))
async def finalize_freeze_action(callback: types.CallbackQuery):
    try:
        student_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        return await callback.answer("❌ Ошибка: неверный формат данных ID", show_alert=True)
    new_date = await process_student_freeze(student_id)

    if new_date:
        formatted_date = new_date.strftime('%d.%m.%Y')
        await callback.answer("✅ Абонемент успешно заморожен!", show_alert=True)

        await callback.message.edit_text(
            f"✅ **Заморозка выполнена успешно!**\n\n"
            f"Абонемент продлен на 5 дней.\n"
            f"Новая дата окончания: **{formatted_date}**\n\n"
            f"❄️ *Статус: Заморожен (разморозится при первом посещении)*",
            parse_mode="Markdown"
        )
    else:
        await callback.answer(
            "❌ Заморозка недоступна.\n"
            "Возможно, она уже была использована для этого абонемента.",
            show_alert=True
        )


@router.callback_query(F.data == "freeze_sub")
async def choose_student_for_freeze(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    # Получаем список всех атлетов этого родителя
    students = await get_all_subscriptions(user_id)

    if not students:
        return await callback.answer("У вас нет зарегистрированных атлетов!", show_alert=True)

    builder = InlineKeyboardBuilder()
    count = 0

    for s in students:
        # Условие: есть дата, она не истекла и can_freeze == 1
        if s.expire_date and s.expire_date > datetime.now() and s.can_freeze > 0:
            builder.row(types.InlineKeyboardButton(
                text=f"❄️ {s.name}",
                # Передаем ID конкретного студента в callback_data
                callback_data=f"confirm_freeze_{s.id}"
            ))
            count += 1

    if count == 0:
        return await callback.answer("Нет активных абонементов, доступных для заморозки (или она уже использована)!",
                                     show_alert=True)

    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="my_profile"))

    await callback.message.edit_text(
        "❄️ **Заморозка абонемента**\n\n"
        "Выберите атлета из списка ниже.\n"
        "Срок действия будет продлен на **5 дней**.\n"
        "⚠️ Заморозка доступна 1 раз за период.",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == 'show_qr')
async def choose_student_for_qr(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    students = await get_student_list(user_id)

    if not students:
        return await callback.answer("У вас нет зарегистрированных атлетов!", show_alert=True)

    builder = InlineKeyboardBuilder()
    for s in students:
        builder.row(InlineKeyboardButton(text=f"🪪 {s.name}", callback_data=f"gen_qr_{s.id}"))

    await callback.message.edit_text("Выберите, чей QR-код сформировать:", reply_markup=builder.as_markup())
    await callback.answer()


LAYOUT_MAP = str.maketrans(
    "йцукенгшщзхъфывапролджэячсмитьбю.ЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ,",
    "qwertyuiop[]asdfghjkl;'zxcvbnm,./QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>?"
)


def fix_layout(text: str) -> str:
    if any(c in "фывапрол" for c in text.lower()):
        return text.translate(LAYOUT_MAP)
    return text


@router.message(F.web_app_data)
async def parse_qr_scan(message: types.Message, raw_data: str):
    raw_data = fix_layout(raw_data)
    logger.info(f"🔍 Обработка данных: {raw_data}")
    parent_to_notify = None
    student_name = "Атлет"
    try:
        parts = raw_data.split(':')
        if len(parts) != 4 or parts[0] != 'student':
            return await message.answer("❌ Ошибка: Неверный формат QR")
        _, scanned_id_str, time_salt, signature = parts
        scanned_id = int(scanned_id_str)
        expected_sig = generate_signature(scanned_id, time_salt)
        if not hmac.compare_digest(signature, expected_sig):
            return await message.answer("🚨 ВНИМАНИЕ: QR-код подделан!")
        now = datetime.now()
        async with AsyncSessionLocal() as session:
            student = await session.get(Student, scanned_id)
            if not student:
                return await message.answer("❌ Атлет не найден в базе!")
            parent_to_notify = student.parent_id
            student_name = student.name
            if student.is_frozen == 1:
                days_actually_frozen = (now - student.last_visit).days
                if days_actually_frozen < 5:
                    days_to_subtract = 5 - days_actually_frozen
                    student.expire_date -= timedelta(days=days_to_subtract)
                    msg = f"❄️ Разморозка! Прошло дней: {days_actually_frozen}. Срок абонемента скорректирован (-{days_to_subtract} дн.)"
                else:
                    msg = f"❄️ Абонемент {student.name} разморожен (5 дней истекли)!"

                student.is_frozen = 0
                await message.answer(msg)

            if student.last_visit and (now - student.last_visit).total_seconds() < 300 and student.is_frozen == 0:
                return await message.answer(f"⚠️ {student.name} уже отмечен! Повтор через 5 мин.")
            if not student.expire_date or student.expire_date < now:
                return await message.answer(
                    f"🔴 ДОСТУП ЗАПРЕЩЕН\n👤 {student.name}\n"
                    f"❌ Истек: {student.expire_date.strftime('%d.%m.%Y') if student.expire_date else 'Нет данных'}"
                )
            student.balance_lessons += 1
            student.last_visit = now  # Теперь здесь время входа
            current_lessons = student.balance_lessons
            expire_str = student.expire_date.strftime('%d.%m.%Y')

            await session.commit()
        response_text = (
            f"🟢 <b>ПРОХОДИТЕ</b>\n"
            f"👤 Атлет: <b>{student_name}</b>\n"
            f"✅ До: {expire_str}\n"
            f"📈 Посещение №{current_lessons}"
        )
        await message.answer(response_text, parse_mode="HTML")
        if parent_to_notify:
            try:
                await message.bot.send_message(
                    parent_to_notify,
                    f"🔔 Вход зафиксирован: <b>{student_name}</b>\nПриятной тренировки! 💪",
                    parse_mode="HTML"
                )
            except Exception:
                pass

    except Exception as e:
        logger.error(f"❌ Ошибка сканера: {e}")
        await message.answer("❌ Ошибка при обработке данных")


@router.message(F.web_app_data)
async def web_app_qr_handler(message: types.Message):
    await parse_qr_scan(message, message.web_app_data.data)


@router.message(F.text.startswith(("student", "ыегвуте")))
async def manual_scanner_handler(message: types.Message):
    await parse_qr_scan(message, message.text)


@router.callback_query(F.data.startswith("gen_qr_"))
async def handle_gen_qr(callback: types.CallbackQuery):
    student_id = int(callback.data.split("_")[-1])
    now = datetime.now()
    time_salt = now.strftime('%Y-%m-%d-%H')
    signature = generate_signature(student_id, time_salt)
    qr_data = f"student:{student_id}:{time_salt}:{signature}"
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(qr_data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    io_bytes = io.BytesIO()
    img.save(io_bytes, format='PNG')
    io_bytes.seek(0)
    photo = BufferedInputFile(io_bytes.getvalue(), filename=f"qr_{student_id}.png")
    await callback.message.answer_photo(
        photo=photo,
        caption=f"🎫 QR-код для входа.\nДействует 1 час, затем нужно обновить."
    )
    await callback.answer()


@router.callback_query(F.data == "add_athlete")
async def start_add_athlete(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите Имя и Фамилию атлета (себя или ребенка):")
    await state.set_state(RegistrationStates.waiting_for_name)
    await callback.answer()


@router.message(RegistrationStates.waiting_for_name)
async def process_athlete_name(message: types.Message, state: FSMContext):
    name = message.text
    user_id = message.from_user.id
    try:
        async with AsyncSessionLocal() as session:
            new_student = Student(
                parent_id=user_id,
                name=name,
                expire_date=None,
                balance_lessons=0,
                can_freeze=1,
                is_frozen=0
            )
            session.add(new_student)
            await session.commit()
        logger.success(f"👤 Добавлен новый атлет: {name} для родителя {user_id}")
        await message.answer(
            f"✅ Атлет <b>{name}</b> успешно зарегистрирован!\n\n"
            "Теперь вы можете купить абонемент или сформировать QR-пропуск в меню.",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard()
        )
        await state.clear()
    except Exception as e:
        logger.error(f"❌ Ошибка при добавлении атлета: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")


@router.callback_query(F.data == 'admin_add_manual')
async def manual_add_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("📝 Введите Имя и Фамилию нового атлета:")
    await state.set_state(AdminManualAdd.waiting_for_name)
    await callback.answer()


@router.message(AdminManualAdd.waiting_for_name)
async def manual_add_process_name(message: types.Message, state: FSMContext):
    await state.update_data(student_name=message.text) # Сохраняем имя в FSM
    await message.answer(
        f"👤 Атлет: <b>{message.text}</b>\n\n"
        "📱 Теперь введите <b>номер телефона пользователя</b>.\n"
        "<i>По этому номеру пользователь сможет войти в свой кабинет.</i>",
        parse_mode="HTML"
    )
    await state.set_state(AdminManualAdd.waiting_for_phone)


#
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


@router.callback_query(F.data == "admin_cash_list")
async def show_all_students_for_cash(callback: types.CallbackQuery):
    async with AsyncSessionLocal() as session:
        stmt = select(Student).order_by(Student.name)
        result = await session.execute(stmt)
        students = result.scalars().all()
    if not students:
        return await callback.answer("В базе пока пусто", show_alert=True)
    builder = InlineKeyboardBuilder()
    for s in students:
        builder.row(InlineKeyboardButton(text=f"👤 {s.name}", callback_data=f"cash_pay_{s.id}"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="admin"))
    await callback.message.edit_text("Выберите атлета для оплаты наличными:", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("cash_pay_"))
async def process_cash_payment(callback: types.CallbackQuery):
    student_id = int(callback.data.split("_")[-1])
    result = await add_abon(student_id)
    if result:
        new_expire, parent_id = result
        await callback.message.edit_text(
            f"✅ <b>Оплата наличными принята!</b>\n"
            f"Абонемент атлета продлен до: <b>{new_expire}</b>",
            parse_mode="HTML"
        )
        if parent_id and parent_id != 0:
            try:
                await callback.bot.send_message(
                    chat_id=parent_id,
                    text=f"💵 <b>Ваша оплата (наличными) подтверждена!</b>\n"
                         f"Абонемент продлен до: <b>{new_expire}</b>. Спасибо!",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление: {e}")
    else:
        await callback.answer("Ошибка: студент не найден", show_alert=True)


@router.callback_query(F.data == "admin_cash_search")
async def cash_search_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("🔍 Введите имя или фамилию для поиска:")
    await state.set_state(AdminManualAdd.waiting_for_search)
    await callback.answer()


@router.message(AdminManualAdd.waiting_for_search)
async def cash_search_results(message: types.Message, state: FSMContext):
    search_query = f"%{message.text}%"
    async with AsyncSessionLocal() as session:
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


@router.callback_query(F.data == "admin_manual_visit")
async def manual_visit_search(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("🔍 Введите имя атлета для отметки:")
    await state.set_state(AdminManualAdd.waiting_for_search_visit)
    await callback.answer()


@router.message(AdminManualAdd.waiting_for_search_visit)
async def manual_visit_results(message: types.Message, state: FSMContext):
    search_query = f"%{message.text}%"
    async with AsyncSessionLocal() as session:
        stmt = select(Student).where(Student.name.ilike(search_query)).order_by(Student.name)
        result = await session.execute(stmt)
        results = result.scalars().all()
    if not results:
        return await message.answer("❌ Никого не нашел.")
    builder = InlineKeyboardBuilder()
    for s in results:
        status = "🟢" if s.expire_date and s.expire_date > datetime.now() else "🔴"
        builder.row(InlineKeyboardButton(
            text=f"{status} {s.name}",
            callback_data=f"manual_checkin_{s.id}")
        )
    await message.answer("Кого отметить?", reply_markup=builder.as_markup())
    await state.clear()


@router.callback_query(F.data.startswith("manual_checkin_"))
async def process_manual_checkin(callback: types.CallbackQuery):
    student_id = int(callback.data.split("_")[-1])
    now = datetime.now()
    student_name = "Атлет"
    parent_id = None
    msg_unfreeze = ""
    async with AsyncSessionLocal() as session:
        student = await session.get(Student, student_id)
        if not student:
            return await callback.answer("Атлет не найден!")
        student_name = student.name
        parent_id = student.parent_id
        if student.is_frozen == 1:
            days_actually_frozen = (now - student.last_visit).days
            if days_actually_frozen < 5:
                days_to_subtract = 5 - days_actually_frozen
                student.expire_date -= timedelta(days=days_to_subtract)
                msg_unfreeze = f"\n❄️ <b>Разморозка!</b> Лишние {days_to_subtract} дн. аннулированы."
            else:
                msg_unfreeze = f"\n❄️ <b>Разморозка!</b> (5 дней истекли)"
            student.is_frozen = 0
        student.balance_lessons += 1
        student.last_visit = now
        current_lessons = student.balance_lessons
        await session.commit()
    await callback.message.edit_text(
        f"✅ <b>Вход отмечен вручную</b>\n"
        f"👤 Атлет: <b>{student_name}</b>\n"
        f"📈 Посещение №{current_lessons}"
        f"{msg_unfreeze}",
        parse_mode="HTML"
    )
    if parent_id and parent_id != 0:
        try:
            await callback.bot.send_message(
                chat_id=parent_id,
                text=f"🔔 <b>Вход зафиксирован (администратором):</b> {student_name}\nПриятной тренировки! 💪",
                parse_mode="HTML"
            )
        except Exception:
            pass

    await callback.answer("Посещение зафиксировано")


@router.callback_query(F.data == 'auth_by_phone')
async def auth_by_phone_call(callback: types.CallbackQuery):
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться номером", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await callback.message.answer(
        "Нажмите кнопку ниже, чтобы подтвердить ваш номер телефона и привязать профиль:",
        reply_markup=kb
    )
    await callback.answer()


@router.message(F.contact)
async def process_user_contact(message: types.Message, session: AsyncSession):
    raw_phone = message.contact.phone_number.replace("+", "")
    clean_phone = raw_phone[-10:]
    stmt = select(Student).where(Student.parent_phone.contains(clean_phone))
    result = await session.execute(stmt)
    students = result.scalars().all()

    if not students:
        await message.answer(
            "❌ Студент с таким номером не найден в нашей базе.\n"
            "Пожалуйста, свяжитесь с администратором, чтобы он внес ваш номер.",
            reply_markup=types.ReplyKeyboardRemove()
        )
        return
    user_id = message.from_user.id
    for student in students:
        student.parent_id = user_id

    try:
        await session.commit()
        names = ", ".join([s.name for s in students])
        await message.answer(
            f"✅ Авторизация успешна!\n"
            f"Привязаны атлеты: <b>{names}</b>\n\n"
            "Теперь вам доступен личный кабинет.",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard()
        )
    except Exception as e:
        logger.error(f"Ошибка при привязке контакта: {e}")
        await message.answer("Произошла ошибка при обновлении данных.")
