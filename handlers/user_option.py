import qrcode
from types import SimpleNamespace
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
import io
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.filters import Command
from sqlalchemy.ext.asyncio import AsyncSession
from database.db import Student, process_student_freeze, Club, User
from config import secret_key
from sqlalchemy import select
from handlers.buttons import get_main_menu_keyboard, get_profile_keyboard, get_section_menu_kb
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, BufferedInputFile
from datetime import timedelta
from aiogram import Router, F, types
from datetime import datetime, date
from loguru import logger
import hashlib
import hmac
from handlers.states import RegistrationStates
from handlers.skud import trigger_dingtian_turnstile


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


@router.message(Command('profile', 'check_status_now'))
@router.callback_query(F.data.in_(['profile', 'check_status_now']))
async def universal_profile_handler(
        event: types.Message | types.CallbackQuery,  # 1. Меняем callback на универсальный event
        session: AsyncSession,
        club: Club,  # Из мидлвари
        club_settings: dict  # Из мидлвари
):
    user_id = event.from_user.id
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

    # Формируем итоговый текст и клавиатуру
    final_text = (
        f"👤 <b>Личный кабинет</b>\n\n{status_text}\n\n"
        "<i>Используйте кнопки ниже для управления:</i>"
    )
    current_user = SimpleNamespace(user_id=user_id, club_id=club.id)
    reply_markup = get_profile_keyboard(current_user, club_settings=club_settings, is_authorized=is_auth)

    try:
        # 2. Проверяем тип события для правильного ответа
        if isinstance(event, types.CallbackQuery):
            # Если это кнопка — плавно редактируем старое сообщение
            await event.message.edit_text(
                text=final_text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
            await event.answer()  # Гасим часики на кнопке
        else:
            # Если это текстовая команда — отправляем новое сообщение в ответ
            await event.answer(
                text=final_text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
            
    except Exception as e:
        if "message is not modified" not in str(e):
            logger.error(f"❌ Ошибка в профиле: {e}")
            # Безопасный ответ на случай падения внутри callback
            if isinstance(event, types.CallbackQuery):
                await event.answer("Ошибка обновления данных")


@router.callback_query(F.data == 'detailed_status_info')
async def detailed_status_handler(
        callback: types.CallbackQuery,
        session: AsyncSession,
        club: Club
):
    user_id = callback.from_user.id
    now = datetime.now()

    # Запрашиваем студентов этого родителя для текущего клуба
    stmt = select(Student).where(
        Student.parent_id == user_id,
        Student.club_id == club.id
    ).order_by(Student.name)

    result = await session.execute(stmt)
    students = result.scalars().all()

    if not students:
        await callback.answer("⚠️ У вас нет привязанных атлетов для просмотра деталей.", show_alert=True)
        return

    # Заголовок нового экрана
    detail_text = f"📊 <b>Подробный статус абонементов</b>\n🏰 Клуб: <b>{club.name}</b>\n\n"

    for s in students:
        # 1. Расчет дней до окончания или сколько дней назад истек
        if s.expire_date:
            days_left = (s.expire_date - now).days
            if days_left >= 0:
                time_info = f"⏳ Осталось дней: <b>{days_left}</b>"
            else:
                time_info = f"🗓 Истёк: <b>{abs(days_left)} дн. назад</b>"
        else:
            time_info = "⏳ Срок действия: <b>не установлен</b>"

        # 2. Форматирование даты последнего визита
        if s.last_visit:
            last_visit_str = f"<b>{s.last_visit.strftime('%d.%m.%Y в %H:%M')}</b>"
        else:
            last_visit_str = "<i>еще не посещал занятия</i>"

        # 3. Форматирование дня рождения
        if s.birthday:
            birthday_str = f"<b>{s.birthday.strftime('%d.%m.%Y')}</b>"
        else:
            birthday_str = "<i>не указан</i>"

        # 4. Логика возможности заморозки (can_freeze)
        freeze_status = "✅ Доступна" if s.can_freeze == 1 else "❌ Недоступна"

        # Собираем блок информации по конкретному студенту
        detail_text += (
            f"👤 Атлет: <b>{s.name}</b>\n"
            f"🆔 ID профиля: <code>{s.id}</code>\n"
            f"🎂 День рождения: {birthday_str}\n"
            f"{time_info}\n"
            f"❄️ Возможность заморозки: {freeze_status}\n"
            f"👟 Последний визит: {last_visit_str}\n"
            f"───────────────────\n\n"
        )

    # Кнопка «Назад», которая использует старый callback 'profile'
    # При нажатии на неё сработает ваш universal_profile_handler и вернет главный экран ЛК
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад в Личный Кабинет", callback_data="profile")]
    ])

    try:
        await callback.message.edit_text(
            text=detail_text,
            reply_markup=back_keyboard,
            parse_mode="HTML"
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"❌ Ошибка в детальном статусе: {e}")
        await callback.answer("Ошибка при загрузке подробных данных")


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
    club: Club
):
    try:
        # Парсим ID студента из колбэка
        student_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        return await callback.answer("❌ Ошибка формата ID", show_alert=True)

    # Достаем шаг заморозки из конфига клуба
    freeze_days = club_settings.get("limits", {}).get("freeze_days_step", 7)

    # ИСПРАВЛЕНО: Порядок аргументов строго соответствует функции бд
    new_date = await process_student_freeze(
        student_id=student_id,
        club_id=club.id,
        club_settings=club_settings,
        session=session,
        days=freeze_days
    )

    # Добавили проверку на отключенный функционал в самом клубе
    if new_date == "disabled":
        return await callback.answer("🚫 В вашем клубе функция заморозки отключена в настройках!", show_alert=True)

    elif new_date:
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
            "Лимит исчерпан, абонемент просрочен или уже заморожен.",
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
        # 5. Проверка прав доступа (Срок действия абонемента)
        if not student.expire_date or student.expire_date < now:
            return await message.answer(f"🔴 ДОСТУП ЗАПРЕЩЕН\n👤 {student_name}\n❌ Срок действия абонемента истек")

        # Проверяем, является ли абонемент безлимитным (используем ваш маркер 999)
        is_unlimited = (student.balance_lessons == 999)

        # Если абонемент НЕ безлимитный, проверяем остаток занятий
        if not is_unlimited:
            if (student.balance_lessons or 0) <= 0:
                return await message.answer(f"🔴 ДОСТУП ЗАПРЕЩЕН\n👤 {student_name}\n❌ На балансе нет занятий")

        # 6. Списание занятия / Формирование вывода баланса
        if is_unlimited:
            # Для безлимита ничего не вычитаем, баланс остается 999
            display_balance = "♾ <b>Режим: Безлимит</b>"
        else:
            # Для обычного абонемента списываем 1 занятие
            student.balance_lessons -= 1
            display_balance = f"🔢 Осталось занятий: <b>{student.balance_lessons}</b>"

        # 7. Фиксация визита
        student.last_visit = now
        await session.commit()  # Сохраняем изменения в базе данных


        #интеграция турникета
        # === ИНТЕГРАЦИЯ ТУРНИКЕТА ===
        # ИСПРАВЛЕНО: Правильное имя ключа "turnstile"
        turnstile_config = club_settings.get("turnstile", {})
        turnstile_opened = False
        turnstile_status = ""
        status_emoji = "🔵"

        # ИСПРАВЛЕНО: Проверяем правильный флаг "enabled"
        if turnstile_config.get("enabled", False):
            turnstile_opened = await trigger_dingtian_turnstile(turnstile_config)

            # ИСПРАВЛЕНО: Исправлены HTML-теги </b> и логика сборки статуса
            if turnstile_opened:
                turnstile_status = "\n✅ <b>Турникет открыт</b>"
                status_emoji = "🟢"
            else:
                turnstile_status = "\n⚠️ <b>Ошибка турникета. Пропустите вручную!</b>"
                status_emoji = "⚠️"

        # ИСПРАВЛЕНО: Переменная turnstile_status и status_emoji добавлены в итоговый текст
        await message.answer(
            f"{status_emoji} <b>ПРОХОДИТЕ</b>\n👤 Атлет: <b>{student_name}</b>\n"
            f"{display_balance}\n"
            f"📅 До: <b>{student.expire_date.strftime('%d.%m.%Y')}</b>"
            f"{turnstile_status}",
            parse_mode='HTML'
        )

        # 8. Уведомление родителю
        if student.parent_id:
            try:
                await message.bot.send_message(
                    chat_id=int(student.parent_id),
                    text=f"🔔 <b>{club.name}</b>: {student_name} вошел в зал.",
                    parse_mode="HTML"
                )
            except Exception as parent_err:
                logger.warning(f"Не удалось отправить уведомление родителю {student.parent_id}: {parent_err}")


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
    await state.clear()  # Сбрасываем старое на всякий случай
    # Сохраняем ID клуба в стейт
    await state.update_data(current_club_id=club.id)

    await callback.message.answer(
        f"📍 Регистрация в клубе: <b>{club.name}</b>\n\n"
        f"Введите <b>Имя и Фамилия</b> атлета (себя или ребенка):",
        parse_mode="HTML"
    )
    await state.set_state(RegistrationStates.waiting_for_name)
    await callback.answer()


# ШАГ 2: Поймали имя -> Запрашиваем ДЕНЬ РОЖДЕНИЯ
@router.message(RegistrationStates.waiting_for_name)
async def process_athlete_name(message: types.Message, state: FSMContext):
    # Требуем, чтобы ввели минимум два слова (Имя и Фамилия), как ты просил в ТЗ!
    parts = message.text.strip().split()
    if len(parts) < 2:
        return await message.answer(
            "⚠️ Пожалуйста, укажите именно <b>Имя и Фамилию</b> через пробел (например: Иван Иванов):",
            parse_mode="HTML"
        )

    await state.update_data(athlete_name=message.text.strip())

    # Переключаем на новый шаг ввода ДР
    await state.set_state(RegistrationStates.waiting_for_birthday)
    await message.answer(
        f"✅ Имя сохранено: <b>{message.text}</b>\n\n"
        f"Введите <b>день рождения атлета</b> в формате ДД.ММ.ГГГГ\n"
        f"<i>(например: 15.08.2012):</i>",
        parse_mode="HTML"
    )


# ШАГ 3: Поймали ДР -> Валидируем и сохраняем в базу PostgreSQL
@router.message(RegistrationStates.waiting_for_birthday)
async def process_athlete_birthday(
        message: types.Message,
        state: FSMContext,
        session: AsyncSession,
        club: Club,
        club_settings: dict
):
    try:
        # Валидируем формат даты и получаем объект datetime.date
        birthday_date = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
    except ValueError:
        return await message.answer(
            "❌ Неверный формат! Введите дату строго в формате ДД.ММ.ГГГГ (например: 25.10.2015):"
        )

    # Достаем накопленные данные из стейта
    data = await state.get_data()
    name = data.get('athlete_name')
    club_id = data.get('current_club_id') or club.id
    user_id = message.from_user.id

    try:
        # 1. ПРОВЕРКА / АВТОПРИСУТСТВИЕ РОДИТЕЛЯ В ТАБЛИЦЕ USERS
        user_stmt = select(User).where(User.user_id == user_id)
        user_exists = (await session.execute(user_stmt)).scalar_one_or_none()

        if not user_exists:
            # Если пользователя нет в БД, создаем его, чтобы избежать ForeignKeyViolationError
            new_user = User(
                user_id=user_id
                # Если в модели User есть другие обязательные поля без default, добавьте их сюда
            )
            session.add(new_user)
            await session.flush()  # Фиксируем изменения в текущей сессии базы данных

        # 2. Создаем атлета со ВСЕМИ привязками и Днём Рождения!
        new_student = Student(
            parent_id=user_id,
            club_id=club_id,
            name=name,
            birthday=birthday_date,  # Передаем ОБЪЕКТ даты
            expire_date=None,
            balance_lessons=0,
            can_freeze=1,
            is_frozen=0,
            last_visit=datetime.now()
        )

        session.add(new_student)
        await session.commit()

        logger.success(f"👤 Клиент сам добавил атлета: {name} с ДР {birthday_date} (Клуб ID: {club_id})")

        # Отправляем в главное меню
        await message.answer(
            f"✅ Атлет <b>{name}</b> успешно зарегистрирован в <b>{club.name}</b>!\n\n"
            "Теперь вы можете купить абонемент или сформировать QR-пропуск в меню.",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(club_settings, club.id)
        )
        await state.clear()

    except Exception as e:
        await session.rollback()
        logger.error(f"❌ Ошибка при самостоятельном добавлении атлета: {e}")
        await message.answer("⚠️ Произошла ошибка при сохранении. Попробуйте позже.")


@router.callback_query(F.data == "auth_by_phone")
async def auth_by_phone_callback(callback: types.CallbackQuery, club: Club):
    builder = ReplyKeyboardBuilder()
    builder.row(types.KeyboardButton(text="📱 Поделиться контактом", request_contact=True))
    await callback.message.answer(
        f"📍 <b>Авторизация в клубе {club.name}</b>\n\n"
        f"Нажмите кнопку <b>«📱 Поделиться контактом»</b> внизу экрана, "
        f"чтобы система проверила ваш номер телефона в базе данных.",
        reply_markup=builder.as_markup(resize_keyboard=True, one_time_keyboard=True),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(F.contact)
async def process_user_contact(
        message: types.Message,
        session: AsyncSession,
        club: Club,
        club_settings: dict
):
    # Очищаем номер от плюсов и пробелов
    raw_phone = message.contact.phone_number.replace("+", "").strip()
    clean_phone_10 = raw_phone[-10:]  # Последние 10 цифр (9991112233)
    user_id = message.from_user.id

    try:
        # Ищем студентов, у которых телефон содержит последние 10 цифр
        stmt = select(Student).where(
            Student.parent_phone.contains(clean_phone_10),
            Student.club_id == club.id
        )
        result = await session.execute(stmt)
        students = result.scalars().all()

        if not students:
            return await message.answer(
                f"❌ Атлеты с номером <code>...{clean_phone_10[-4:]}</code> не найдены в базе клуба <b>{club.name}</b>.\n\n"
                "Свяжитесь с администратором, чтобы он внес ваш номер в систему.",
                parse_mode="HTML",
                reply_markup=types.ReplyKeyboardRemove()
            )

        # Безопасный импорт модели User, чтобы избежать падения, если пути отличаются
        try:
            from database.db import User
        except ImportError:
            from database.db import User  # Если модель лежит в другом месте, поправьте путь

        # Проверяем, зарегистрирован ли уже этот user_id в таблице users
        user_stmt = select(User).where(User.user_id == user_id)
        user_exists = (await session.execute(user_stmt)).scalar_one_or_none()

        if not user_exists:
            # Создаем запись для нового пользователя, чтобы не нарушать внешний ключ (ForeignKey)
            new_user = User(user_id=user_id)
            session.add(new_user)
            await session.flush()  # Синхронизируем с базой, чтобы ID зафиксировался

        # Привязываем Telegram ID ко всем найденным карточкам атлетов
        for student in students:
            student.parent_id = user_id
            session.add(student)

        await session.commit()
        names = ", ".join([f"<b>{s.name}</b>" for s in students])

        await message.answer(
            f"✅ Авторизация в <b>{club.name}</b> успешна!\n\n"
            f"Привязаны атлеты: {names}\n\n"
            "Теперь вам доступен личный кабинет и QR-пропуск.",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard(club_settings, club.id)
        )

    except Exception as e:
        await session.rollback()
        logger.error(f"❌ Ошибка авторизации в клубе {club.id}: {e}")
        # Если была тишина, отправляем сообщение пользователю, чтобы он видел ошибку
        await message.answer("⚠️ Произошла внутренняя ошибка привязки профиля. Пожалуйста, сообщите администратору.")


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

        # 2.5 ЗАЩИТА ОТ ОШИБКИ FOREIGN KEY (parent_id)
        # Гарантируем, что юзер существует в таблице users перед привязкой к нему студентов
        user_stmt = select(User).where(User.user_id == user_id)
        user_exists = (await session.execute(user_stmt)).scalar_one_or_none()

        if not user_exists:
            # Создаем запись для нового родителя, чтобы PostgreSQL разрешил связь таблиц
            new_user = User(
                user_id=user_id
                # Если в твоей модели User поле для телефона называется по-другому, поправь имя:
                # phone=raw_phone
            )
            session.add(new_user)
            await session.flush()  # Фиксируем юзера в сессии, чтобы его ID стал валидным для Студента

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
    # 1. Извлекаем код (например: schedule_boxing -> boxing)
    discipline_code = callback.data.split('_')[1].lower()
    
    # 2. Лезем в конфиг за данными этой секции
    disciplines = club_settings.get("disciplines", {})
    discipline_cfg = disciplines.get(discipline_code)

    if not discipline_cfg:
        return await callback.answer("Упс! Данные этой секции не найдены 🛠", show_alert=True)

    name = discipline_cfg.get("name", discipline_code.upper())
    schedule_data = discipline_cfg.get("schedule")

    # 3. Парсим наше новое структурированное JSONB-расписание
    # Если там старая строка или вообще пусто — выводим красивую заглушку
    if not schedule_data or isinstance(schedule_data, str):
        final_schedule_text = "⏳ <i>Расписание временно не заполнено администратором.</i>"
    else:
        # Словарь для перевода ключей дней недели на человеческий русский
        day_translations = {
            "mon": "Понедельник", "tue": "Вторник", "wed": "Среда", 
            "thu": "Четверг", "fri": "Пятница", "sat": "Суббота", "sun": "Воскресенье"
        }
        
        lines = []
        # Проходим строго по порядку дней недели
        for day_key in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]:
            day_lessons = schedule_data.get(day_key, [])
            
            # Если в этот день есть тренировки — красиво их форматируем
            if day_lessons:
                lines.append(f"📌 <b>{day_translations[day_key]}:</b>")
                for lesson in day_lessons:
                    # Считаем свободные места (всего минус занято)
                    free_slots = max(0, lesson.get("max_slots", 15) - lesson.get("taken_slots", 0))
                    
                    lines.append(
                        f"  ⏱ <code>{lesson['time']}</code> — {lesson['coach']}\n"
                        f"  └ 👥 Мест осталось: <b>{free_slots}</b> из {lesson.get('max_slots', 15)}\n"
                    )
                lines.append("") # Делаем пустую строку-отступ между днями для читаемости
                
        # Собираем все строки вместе. Если массив пустой — значит админ завел структуру, но уроков 0
        if lines:
            final_schedule_text = "\n".join(lines)
        else:
            final_schedule_text = "⏳ <i>Расписание на эту неделю пока не заполнено.</i>"

    # 4. Выводим итоговый красивый результат пользователю (атлету или родителю)
    await callback.message.edit_text(
        text=(
            f"📅 <b>Расписание секции: {name}</b>\n\n"
            f"{final_schedule_text}\n"
            "<i>Используйте меню ниже для записи на занятия:</i>"
        ),
        reply_markup=get_section_menu_kb(discipline_code, name),
        parse_mode="HTML"
    )

    await callback.answer()
    
    
@router.callback_query(F.data == "show_android_instructions")
async def process_android_instruction(callback: types.CallbackQuery):
    """
    Ловит нажатие кнопки Android и отправляет инструкцию в чат.
    """
    instruction_text = (
        "🤖 **Инструкция для Android:**\n\n"
        "1. Перейди прямо сейчас в профиль нашего бота (нажми на его аватарку или имя в самом верху экрана).\n"
        "2. В правом верхнем углу профиля нажмите на **три вертикальные точки** (меню).\n"
        "3. Выбери пункт **«Добавить на главный экран»** (Add to Home screen).\n\n"
        "📸 _Иконка с логотипом создастся автоматически! Если захочешь её изменить, можешь сделать скриншот логотипа прямо из этого чата._\n\n"
        "🔥 Готово! Теперь бот всегда под рукой."
    )
    
    # Отправляем текст в чат
    await callback.message.answer(text=instruction_text, parse_mode="Markdown")
    
    # Гасим часики на кнопке, чтобы она не «зависала»
    await callback.answer()



