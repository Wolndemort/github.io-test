import qrcode
from aiogram.exceptions import TelegramAPIError
from aiogram.utils.keyboard import InlineKeyboardBuilder
import io
from sqlalchemy.ext.asyncio import AsyncSession
from database.db import get_student_list, get_all_subscriptions, Student, process_student_freeze, AsyncSessionLocal
from config import secret_key
from sqlalchemy import select
from handlers.buttons import get_bjj_keyboard, get_kids_keyboard, get_main_menu_keyboard, get_profile_keyboard
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, BufferedInputFile, ReplyKeyboardMarkup, KeyboardButton
from datetime import timedelta
from aiogram import Router, F, types
from datetime import datetime
from loguru import logger
import hashlib
import hmac
from handlers.states import RegistrationStates


def generate_signature(user_id, time_salt):
    msg = f"{user_id}:{time_salt}".encode()
    signature = hmac.new(
        secret_key.encode(),
        msg,
        hashlib.sha256
    ).hexdigest()
    return signature[:10]


router = Router()


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
async def universal_profile_handler(callback: types.CallbackQuery, session: AsyncSession):
    user_id = callback.from_user.id
    now = datetime.now()
    logger.debug(f"🔍 Запрос профиля для ID: {user_id}")
    try:
        students = await get_all_subscriptions(user_id, session)
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
                elif getattr(s, 'is_frozen', 0) == 1:  # Проверка на заморозку
                    status = "❄️ <b>ЗАМОРОЖЕН</b>"
                elif s.expire_date > now:
                    status = f"✅ <b>Активен</b> до <code>{s.expire_date.strftime('%d.%m.%Y')}</code>"
                else:
                    status = f"🔴 <b>ИСТЕК</b> (<code>{s.expire_date.strftime('%d.%m.%Y')}</code>)"

                balance = getattr(s, 'balance_lessons', 0) or 0
                if balance >= 900:
                    lessons_info = "♾ <b>Безлимит</b>"
                elif balance > 0:
                    lessons_info = f"🔢 Занятий: <b>{balance}</b>"
                else:
                    lessons_info = "❌ <b>Занятия окончены</b>"

                phone_info = f" [📞 {s.parent_phone}]" if s.parent_phone else " [📱 нет номера]"
                status_text += f"\n• <b>{s.name}</b>: {status} {lessons_info}\n {phone_info}"

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
        except TelegramAPIError:
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


@router.callback_query(F.data.startswith("confirm_freeze_"))
async def finalize_freeze_action(callback: types.CallbackQuery, session: AsyncSession):
    try:
        student_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        return await callback.answer("❌ Ошибка: неверный формат данных ID", show_alert=True)
    new_date = await process_student_freeze(student_id, session)

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
async def choose_student_for_freeze(callback: types.CallbackQuery, session: AsyncSession):
    user_id = callback.from_user.id
    students = await get_all_subscriptions(user_id, session)
    if not students:
        return await callback.answer("У вас нет зарегистрированных атлетов!", show_alert=True)
    builder = InlineKeyboardBuilder()
    count = 0
    for s in students:
        if s.expire_date and s.expire_date > datetime.now() and s.can_freeze > 0:
            builder.row(types.InlineKeyboardButton(
                text=f"❄️ {s.name}",
                callback_data=f"confirm_freeze_{s.id}"
            ))
            count += 1
    if count == 0:
        return await callback.answer("Нет активных абонементов, доступных для заморозки (или она уже использована)!",
                                     show_alert=True)
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="profile"))
    await callback.message.edit_text(
        "❄️ **Заморозка абонемента**\n\n"
        "Выберите атлета из списка ниже.\n"
        "Срок действия будет продлен на **5 дней**.\n"
        "⚠️ Заморозка доступна 1 раз за период.",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == 'show_qr')
async def choose_student_for_qr(callback: types.CallbackQuery, session: AsyncSession):
    user_id = callback.from_user.id
    students = await get_student_list(user_id, session)
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
async def parse_qr_scan(message: types.Message):
    raw_data = message.web_app_data.data
    raw_data = fix_layout(raw_data)
    logger.info(f"🔍 Обработка данных: {raw_data}")
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
            student_name = str(student.name)
            was_frozen = False
            if student.is_frozen == 1:
                student.is_frozen = 0
                was_frozen = True
                session.add(student)
                await message.answer(f"❄️ Абонемент {student_name} разморожен!")
                last_v = student.last_visit or now
                days_actually_frozen = (now - last_v).days
                if days_actually_frozen < 5:
                    days_to_subtract = 5 - days_actually_frozen
                    if student.expire_date:
                        student.expire_date -= timedelta(days=days_to_subtract)
            if student.last_visit and (now - student.last_visit).total_seconds() < 300:
                if was_frozen:
                    await session.commit()
                return await message.answer(f"⚠️ {student_name} уже отмечен! Повтор через 5 мин.")
            if not student.expire_date or student.expire_date < now:
                await session.commit()
                return await message.answer(f"🔴 ДОСТУП ЗАПРЕЩЕН\n👤 {student_name}\n❌ Срок истек")
            if (student.balance_lessons or 0) <= 0:
                await session.commit()
                return await message.answer(f"🔴 ДОСТУП ЗАПРЕЩЕН\n👤 {student_name}\n❌ Нет занятий")
            if student.balance_lessons < 900:
                student.balance_lessons -= 1
            student.last_visit = now
            await session.commit()
            await message.answer(f"🟢 <b>ПРОХОДИТЕ</b>\n👤 Атлет: <b>{student_name}</b>", parse_mode="HTML")
            if parent_to_notify:
                try:
                    await message.bot.send_message(int(parent_to_notify), f"🔔 Вход: {student_name}")
                except TelegramAPIError:
                    pass

    except Exception as e:
        logger.error(f"❌ Ошибка сканера: {e}")
        await message.answer("❌  Ошибка при обработке данных")


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
    img.save(io_bytes, "PNG")
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
async def process_athlete_name(message: types.Message, state: FSMContext, session: AsyncSession):
    name = message.text
    user_id = message.from_user.id
    try:
        new_student = Student(
            parent_id=user_id,
            name=name,
            expire_date=None,
            balance_lessons=0,
            can_freeze=1,
            is_frozen=0,
            last_visit=datetime.now()
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
        await session.rollback()
        logger.error(f"❌ Ошибка при добавлении атлета: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")


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
    try:
        stmt = select(Student).where(Student.parent_phone.contains(clean_phone))
        result = await session.execute(stmt)
        students = result.scalars().all()
        if not students:
            return await message.answer(
                "❌ Студент с таким номером не найден в нашей базе.\n"
                "Пожалуйста, свяжитесь с администратором, чтобы он внес ваш номер.",
                reply_markup=types.ReplyKeyboardRemove()
            )
        user_id = message.from_user.id
        for student in students:
            student.parent_id = user_id
            session.add(student)
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
        await session.rollback()
        logger.error(f"❌ Ошибка при привязке контакта: {e}")
        await message.answer("Произошла ошибка при обновлении данных. Попробуйте позже.")
