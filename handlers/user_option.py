import qrcode
from aiogram.utils.keyboard import InlineKeyboardBuilder
import io
from sqlalchemy.ext.asyncio import AsyncSession
from database.db import Student, process_student_freeze, Club
from config import secret_key
from sqlalchemy import select
from handlers.buttons import get_main_menu_keyboard, get_profile_keyboard, get_section_menu_kb
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, BufferedInputFile
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
async def go_to_begin(
        callback: types.CallbackQuery,
        state: FSMContext,
        club: Club,  # <--- Достаем из мидлвари
        club_settings: dict  # <--- Достаем из мидлвари
):
    await state.clear()
    user_name = callback.from_user.first_name

    # Берем название клуба из настроек UI
    club_name = club_settings.get("ui", {}).get("club_name", club.name)

    await callback.message.edit_text(
        text=f"<b>{club_name}</b>\n\nС возвращением, {user_name}! Чем я могу помочь?",
        parse_mode="HTML",
        # ПЕРЕДАЕМ АРГУМЕНТЫ В КЛАВУ:
        reply_markup=get_main_menu_keyboard(club_settings, club.id)
    )
    await callback.answer()


@router.callback_query(F.data.in_(['profile', 'check_status_now']))
async def universal_profile_handler(
        callback: types.CallbackQuery,
        session: AsyncSession,
        club: Club,  # Из мидлвари
        club_settings: dict  # Из мидлвари
):
    user_id = callback.from_user.id
    now = datetime.now()

    # 1. Изоляция: тянем студентов ТОЛЬКО этого родителя и ТОЛЬКО этого клуба
    stmt = select(Student).where(
        Student.parent_id == user_id,
        Student.club_id == club.id
    ).order_by(Student.name)

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
            # Логика статуса даты
            if not s.expire_date:
                status = "❌ <b>Не куплен</b>"
            elif getattr(s, 'is_frozen', 0) == 1:
                status = "❄️ <b>ЗАМОРОЖЕН</b>"
            elif s.expire_date > now:
                status = f"✅ <b>Активен</b> до <code>{s.expire_date.strftime('%d.%m.%Y')}</code>"
            else:
                status = f"🔴 <b>ИСТЕК</b> (<code>{s.expire_date.strftime('%d.%m.%Y')}</code>)"

            # Логика баланса (SaaS-friendly: 999 = Безлимит)
            balance = getattr(s, 'balance_lessons', 0)
            if balance >= 900:
                lessons_info = "♾ <b>Безлимит</b>"
            elif balance > 0:
                lessons_info = f"🔢 Занятий: <b>{balance}</b>"
            else:
                lessons_info = "❌ <b>Занятия окончены</b>"

            status_text += f"\n• <b>{s.name}</b>: {status}\n  └ {lessons_info}"

    try:
        # Вызываем твою динамическую клавиатуру (передаем настройки клуба!)
        await callback.message.edit_text(
            text=f"👤 <b>Личный кабинет</b>\n\n{status_text}\n\n"
                 "<i>Используйте кнопки ниже для управления:</i>",
            reply_markup=get_profile_keyboard(club_settings=club_settings, is_authorized=is_auth),
            parse_mode="HTML"
        )
    except Exception as e:
        if "message is not modified" not in str(e):
            logger.error(f"❌ Ошибка в профиле: {e}")
            await callback.answer("Ошибка обновления данных")

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
    club: Club  # 1. ДОБАВИЛИ ТИП (Club)
):
    try:
        # Парсим ID студента из колбэка
        student_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        return await callback.answer("❌ Ошибка формата ID", show_alert=True)

    # Достаем шаг заморозки из конфига клуба
    freeze_days = club_settings.get("limits", {}).get("freeze_days_step", 7)

    # 2. ИСПРАВЛЕННЫЙ ВЫЗОВ ФУНКЦИИ БАЗЫ:
    # Передаем саму сессию (session), а не тип (AsyncSession)
    new_date = await process_student_freeze(
        student_id=student_id,
        session=session,
        days=freeze_days,
        club_id=club.id,
        club_settings=club_settings

    )

    if new_date:
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
            "Лимит исчерпан или абонемент неактивен.",
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
            f"❄️ <b>Заморозка абонемента ({club.name})</b>\n\n"
            f"Выберите атлета. Срок действия будет продлен на <b>{freeze_days} дней</b>.\n\n"
            f"⚠️ <i>Заморозка доступна 1 раз за период. Абонемент разморозится автоматически при первом входе в зал.</i>"
        ),
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


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


@router.message(F.web_app_data)
async def parse_qr_scan(
        message: types.Message,
        session: AsyncSession,  # Из мидлвари
        club: Club,  # Из мидлвари
        club_settings: dict  # Из мидлвари
):
    raw_data = fix_layout(message.web_app_data.data)
    logger.info(f"🔍 Сканер клуба {club.name} (ID:{club.id}): {raw_data}")

    try:
        # 1. Валидация формата и подписи
        parts = raw_data.split(':')
        if len(parts) != 4 or parts[0] != 'student':
            return await message.answer("❌ Ошибка: Неверный формат QR")

        _, scanned_id_str, time_salt, signature = parts
        scanned_id = int(scanned_id_str)

        if not hmac.compare_digest(signature, generate_signature(scanned_id, time_salt)):
            return await message.answer("🚨 ВНИМАНИЕ: QR-код подделан!")

        now = datetime.now()

        # 2. Поиск атлета с проверкой клуба (КРИТИЧНО ДЛЯ SAAS)
        student = await session.get(Student, scanned_id)
        if not student or student.club_id != club.id:
            return await message.answer(f"❌ Атлет не найден в базе клуба {club.name}!")

        student_name = str(student.name)

        # 3. Логика разморозки (Берем дни из конфига)
        if student.is_frozen == 1:
            student.is_frozen = 0
            # Вычисляем шаг заморозки из конфига (по дефолту 7)
            freeze_step = club_settings.get("limits", {}).get("freeze_days_step", 7)

            # Если размораживается раньше времени — корректируем дату
            days_passed = (now - (student.last_visit or now)).days
            if days_passed < freeze_step:
                diff = freeze_step - days_passed
                if student.expire_date:
                    student.expire_date -= timedelta(days=diff)

            await message.answer(f"❄️ Абонемент {student_name} РАЗМОРОЖЕН")

        # 4. Анти-флуд (5 минут)
        if student.last_visit and (now - student.last_visit).total_seconds() < 300:
            return await message.answer(f"⚠️ {student_name} уже в зале! (Повтор через 5 мин)")

        # 5. Проверка прав доступа (Срок и Баланс)
        if not student.expire_date or student.expire_date < now:
            return await message.answer(f"🔴 ДОСТУП ЗАПРЕЩЕН\n👤 {student_name}\n❌ Срок истек")

        if (student.balance_lessons or 0) <= 0:
            return await message.answer(f"🔴 ДОСТУП ЗАПРЕЩЕН\n👤 {student_name}\n❌ Нет занятий")

        # 6. Списание занятия (SaaS-friendly: 900+ это безлимит)
        if student.balance_lessons < 900:
            student.balance_lessons -= 1
            display_balance = f"🔢 Осталось занятий: <b>{student.balance_lessons}</b>"
        else:
            display_balance = "♾ <b>Безлимит</b>"

        # 7. Фиксация визита
        student.last_visit = now
        await session.commit()  # Сохраняем всё одним махом

        await message.answer(
            f"🟢 <b>ПРОХОДИТЕ</b>\n👤 Атлет: <b>{student_name}</b>\n"
            f"{display_balance}\n"
            f"📅 До: <b>{student.expire_date.strftime('%d.%m.%Y')}</b>",
            parse_mode='HTML'
        )

        # 8. Уведомление родителю
        if student.parent_id:
            try:
                # Добавляем название клуба в уведомление родителя
                await message.bot.send_message(
                    chat_id=int(student.parent_id),  # <--- Используй объект с маленькой буквы!
                    text=f"🔔 <b>{club.name}</b>: {student_name} вошел в зал.",
                    parse_mode="HTML"
                )
            except Exception:
                pass

    except Exception as e:
        logger.error(f"❌ Ошибка сканера: {e}")
        await message.answer("❌ Ошибка при обработке сканирования")


@router.message(F.web_app_data)
async def web_app_qr_handler(
    message: types.Message,
    session: AsyncSession,
    club: Club,
    club_settings: dict
):
    # ПЕРЕДАЕМ ВСЁ: и сообщение, и сессию, и конфиги клуба
    await parse_qr_scan(
        message=message,
        session=session,
        club=club,
        club_settings=club_settings
    )


@router.message(F.text.startswith(("student", "ыегвуте")))
async def manual_scanner_handler(
        message: types.Message,
        session: AsyncSession,  # Прилетело из мидлвари
        club: Club,  # Прилетело из мидлвари
        club_settings: dict  # Прилетело из мидлвари
):
    # 1. Фикс раскладки (если ввели "ыегвуте" вместо "student")
    raw_data = fix_layout(message.text)

    # 2. Вызываем наш "огромный" хендлер, ПЕРЕДАВАЯ ВСЕ ДАННЫЕ
    # Теперь parse_qr_scan внутри себя проверит, что атлет из ЭТОГО клуба
    await parse_qr_scan(
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
    now = datetime.now()
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
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="🔙 Назад к списку", callback_data="show_qr"))

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


@router.callback_query(F.data == "add_athlete")
async def start_add_athlete(callback: types.CallbackQuery, state: FSMContext, club: Club):
    # Сохраняем ID клуба в стейт, чтобы на следующем шаге знать, куда привязать атлета
    await state.update_data(current_club_id=club.id)

    await callback.message.answer(
        f"📍 Регистрация в клубе: <b>{club.name}</b>\n\n"
        f"Введите Имя и Фамилию атлета (себя или ребенка):",
        parse_mode="HTML"
    )
    await state.set_state(RegistrationStates.waiting_for_name)
    await callback.answer()


@router.message(RegistrationStates.waiting_for_name)
async def process_athlete_name(
        message: types.Message,
        state: FSMContext,
        session: AsyncSession,
        club: Club,  # Из мидлвари
        club_settings: dict  # Из мидлвари
):
    name = message.text
    user_id = message.from_user.id

    # 1. Достаем ID клуба из стейта (или напрямую из аргумента club)
    data = await state.get_data()
    club_id = data.get('current_club_id') or club.id

    try:
        # 2. Создаем атлета С ПРИВЯЗКОЙ К КЛУБУ (club_id)
        new_student = Student(
            parent_id=user_id,
            club_id=club_id,  # <--- САМОЕ ВАЖНОЕ ДЛЯ SAAS
            name=name,
            expire_date=None,
            balance_lessons=0,
            can_freeze=1,  # Можно брать дефолт из club_settings["limits"]
            is_frozen=0,
            last_visit=datetime.now()
        )

        session.add(new_student)
        await session.commit()

        logger.success(f"👤 Добавлен атлет: {name} (Клуб ID: {club_id}) для {user_id}")

        # 3. Отправляем в главное меню, передавая настройки клуба
        await message.answer(
            f"✅ Атлет <b>{name}</b> успешно зарегистрирован в <b>{club.name}</b>!\n\n"
            "Теперь вы можете купить абонемент или сформировать QR-пропуск в меню.",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(club_settings, club.id)  # <--- ФИКС ОШИБКИ UNFILLED
        )
        await state.clear()

    except Exception as e:
        await session.rollback()
        logger.error(f"❌ Ошибка при добавлении атлета: {e}")
        await message.answer("⚠️ Произошла ошибка. Попробуйте позже.")


@router.message(F.contact)
async def process_user_contact(
        message: types.Message,
        session: AsyncSession,
        club: Club,  # Из мидлвари
        club_settings: dict  # Из мидлвари
):
    # 1. Очистка номера (берем последние 10 цифр для надежности)
    raw_phone = message.contact.phone_number.replace("+", "")
    clean_phone = raw_phone[-10:]
    user_id = message.from_user.id

    try:
        # 2. ИЗОЛЯЦИЯ: Ищем студентов по номеру ТОЛЬКО в этом клубе
        # Чтобы не привязать атлетов из другого зала по ошибке
        stmt = select(Student).where(
            Student.parent_phone.contains(clean_phone),
            Student.club_id == club.id  # <--- КЛЮЧЕВОЙ ФИЛЬТР ДЛЯ SAAS
        )
        result = await session.execute(stmt)
        students = result.scalars().all()

        if not students:
            return await message.answer(
                f"❌ Атлеты с номером <code>...{clean_phone[-4:]}</code> не найдены в базе клуба <b>{club.name}</b>.\n\n"
                "Свяжитесь с администратором, чтобы он внес ваш номер в систему.",
                parse_mode="HTML",
                reply_markup=types.ReplyKeyboardRemove()
            )

        # 3. Привязываем Telegram ID к найденным карточкам атлетов
        for student in students:
            student.parent_id = user_id
            session.add(student)

        await session.commit()

        names = ", ".join([f"<b>{s.name}</b>" for s in students])

        # 4. Убираем "желтые" ошибки PyCharm: передаем конфиги в клавиатуру
        await message.answer(
            f"✅ Авторизация в <b>{club.name}</b> успешна!\n\n"
            f"Привязаны атлеты: {names}\n\n"
            "Теперь вам доступен личный кабинет и QR-пропуск.",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(club_settings, club.id)  # <--- ФИКС ОШИБОК
        )

    except Exception as e:
        await session.rollback()
        logger.error(f"❌ Ошибка авторизации в клубе {club.id}: {e}")
        await message.answer("⚠️ Ошибка привязки. Попробуйте позже.")


@router.callback_query(F.data.startswith('schedule_'))
async def show_discipline_schedule(
        callback: types.CallbackQuery,
        club_settings: dict
):
    # 1. Извлекаем код (префикс schedule_ из кнопки)
    # Например: schedule_boxing -> boxing
    discipline_code = callback.data.split('_')[1].lower()
    # 2. Лезем в конфиг за данными этой секции
    disciplines = club_settings.get("disciplines", club_settings)
    discipline_cfg = disciplines.get(discipline_code)

    if not discipline_cfg:
        return await callback.answer("Упс! Данные этой секции не найдены 🛠", show_alert=True)

    # 3. Достаем название и расписание
    name = discipline_cfg.get("name", discipline_code.upper())
    # Если в конфиге "1111", то выведем это, если пусто — заглушку
    schedule_text = discipline_cfg.get("schedule")

    if not schedule_text or schedule_text == "":
        schedule_text = "Расписание временно не заполнено администратором ⏳"

    await callback.message.edit_text(
        text=(
            f"📅 <b>Расписание: {name}</b>\n\n"
            f"{schedule_text}\n\n"
            "<i>Выберите действие:</i>"
        ),
        reply_markup=get_section_menu_kb(discipline_code, name),
        parse_mode="HTML"
    )

    await callback.answer()
