from sqlalchemy.ext.asyncio import AsyncSession
from handlers.states import AdminStates, AdminSettings
from sqlalchemy import update
from redis.asyncio import Redis

import pandas as pd
import os
from aiogram.types import FSInputFile
import asyncio
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database.db import get_all_users_count, get_active_subs_count, User, get_daily_stats, Student, \
    Club
from sqlalchemy import select
from handlers.buttons import admin_keyboard, get_scanner_keyboard
from handlers.states import AdminManualAdd
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from datetime import timedelta
from aiogram import Router, F, types
from datetime import datetime
from loguru import logger


router = Router()


# Фильтр теперь пускает И тебя, И владельца конкретного клуба
@router.message(Command('admin'))
@router.callback_query(F.data == "admin")
async def admin_panel(
        event: types.Message | types.CallbackQuery,
        club: Club,  # Из Middleware
        club_settings: dict,  # Из Middleware
        is_owner: bool,  # Из Middleware (исправил имя аргумента)
        is_super_admin: bool,  # Из Middleware
        session: AsyncSession
):
    # Проверка: если это не владелец и не ты — игнорим
    if not (is_owner or is_super_admin):
        return

    # Стандартный фикс для Message/Callback
    message = event.message if isinstance(event, types.CallbackQuery) else event
    if isinstance(event, types.CallbackQuery):
        await event.answer()

    try:
        all_users = await get_all_users_count(club_id=club.id, session=session)
        active_subs = await get_active_subs_count(club_id=club.id, session=session)
        club_name = club_settings.get("ui", {}).get("club_name") or club.name

        text = (
            f"📈 <b>Панель управления: {club_name}</b>\n\n"
            f"👥 Всего пользователей: <code>{all_users}</code>\n"
            f"💳 Активных абонементов: <code>{active_subs}</code>\n\n"
            "Чего желаете, босс?"
        )
        kb_scanner = get_scanner_keyboard(club_id=club.id, club_settings=club_settings)
        await message.answer(text, reply_markup=admin_keyboard(club_id=club.id, club_settings=club_settings))

    except Exception as e:
        logger.error(f"❌ Ошибка в админ-панели клуба {club.id}: {e}")


@router.callback_query(F.data == "admin_keyboard")
async def back_to_admin_main_menu(
    callback: types.CallbackQuery,
    club_settings: dict,
    club: Club,           # Объект из мидлвари
    is_owner: bool,
    is_super_admin: bool
):
    await callback.message.edit_text(
        f"🏠 <b>Панель управления клуба {club.name}</b>\n"
        f"Выберите нужный раздел:",
        reply_markup=admin_keyboard(club_settings, club.id),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "admin_settings")
async def admin_settings_menu(callback: types.CallbackQuery, club_settings: dict):
    builder = InlineKeyboardBuilder()
    features = club_settings.get("features", {})
    limits = club_settings.get("limits", {})

    # Список системных кнопок (ключ в JSON : название на кнопке)
    buttons = {
        "freeze": "Заморозка",
        "qr_checkin": "QR-вход",
        "manual_add": "Ручное добавление"
    }

    for key, label in buttons.items():
        status = "✅" if features.get(key, True) else "❌"
        builder.row(types.InlineKeyboardButton(
            text=f"{status} {label}",
            callback_data=f"toggle_feat_{key}")  # Префикс для системных фич
        )
    sub_days = limits.get("subscription_days", 30)
    builder.row(types.InlineKeyboardButton(
        text=f"⏳ Срок абона: {sub_days} дн.",
        callback_data="edit_sub_days"  # Ведет на выбор 30/60/90
    ))
    builder.row(types.InlineKeyboardButton(
        text="💳 Изменить реквизиты",
        callback_data="admin_edit_payments")
    )
    builder.row(types.InlineKeyboardButton(text="🥋 Управление секциями", callback_data="manage_disciplines"))
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_keyboard"))

    await callback.message.edit_text(
        "🛠 <b>Настройки модулей клуба</b>\n\nВключайте и выключайте функции бота:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "edit_sub_days")
async def edit_sub_days_choice(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    # Популярные пресеты
    for days in [14, 30, 45, 60, 90]:
        builder.button(text=f"{days} дн.", callback_data=f"set_sub_days_{days}")

    builder.adjust(3)
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_settings"))

    await callback.message.edit_text(
        "⏳ <b>Выберите срок действия абонемента:</b>\n"
        "Это число дней будет добавляться при каждой оплате.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("set_sub_days_"))
async def save_sub_days(
        callback: types.CallbackQuery,
        club: Club,  # <--- Достаем объект целиком, как в Middleware
        club_settings: dict,
        session: AsyncSession,
        redis: Redis

):
    # 1. Парсим дни
    try:
        new_days = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        return await callback.answer("Ошибка данных", show_alert=True)

    # 2. Обновляем JSON (безопасно через setdefault)
    club_settings.setdefault("limits", {})["subscription_days"] = new_days

    # 3. Сохраняем в базу (используем club.id)
    await session.execute(
        update(Club)
        .where(Club.id == club.id)
        .values(club_settings=club_settings)
    )
    await session.commit()
    await redis.delete(f"club_config:{callback.bot.token}")
    await callback.answer(f"✅ Срок изменен на {new_days} дн.")

    # Возвращаемся в меню настроек
    # Важно: прокидываем обновленный club_settings, чтобы кнопка сразу показала новое число
    await admin_settings_menu(callback, club_settings)


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


@router.callback_query(F.data.startswith("toggle_"))
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
        await admin_settings_menu(callback, club_settings)
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
    club: Club,              # <--- Исправлено
    club_settings: dict,     # <--- Исправлено
    is_owner: bool,          # <--- Исправлено
    is_super_admin: bool,    # <--- Исправлено
    session: AsyncSession
):
    if not (is_owner or is_super_admin):
        return await callback.answer("❌ Доступ ограничен.")

    try:
        # 🛡️ Передаем club.id
        visits, active = await get_daily_stats(club_id=club.id, session=session)

        report_text = (
            f"📊 <b>ОТЧЕТ: {club.name}</b>\n"
            f"📅 Дата: {datetime.now().strftime('%d.%m.%Y')}\n\n"
            f"👤 <b>Посещений сегодня:</b> <code>{visits}</code>\n"
            f"💎 <b>Активных абонементов:</b> <code>{active}</code>\n\n"
            f"<i>Обновлено в {datetime.now().strftime('%H:%M')}</i>"
        )

        await callback.message.edit_text(
            text=report_text,
            # Не забывай прокидывать club.id в клавиатуру, если она того требует
            reply_markup=admin_keyboard(club_id=club.id, club_settings=club_settings),
            parse_mode="HTML"
        )

    except Exception as e:
        if "message is not modified" in str(e).lower():
            await callback.answer("Данные актуальны ✅")
        else:
            logger.error(f"❌ Ошибка отчета (Клуб {club.id}): {e}")
            await callback.answer("⚠️ Ошибка статистики", show_alert=True)


@router.callback_query(F.data == 'admin_add_manual')
async def manual_add_start(
        callback: types.CallbackQuery,
        state: FSMContext,
        is_owner: bool,  # <--- Поправил под Middleware
        is_super_admin: bool  # <--- Поправил под Middleware
):
    # Проверка прав
    if not (is_owner or is_super_admin):
        return await callback.answer("❌ У вас нет прав администратора.", show_alert=True)

    # Ставим стейт
    await state.set_state(AdminManualAdd.waiting_for_name)

    # Кнопка отмены (чтобы не застрять в FSM)
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Отмена", callback_data="admin")  # Возврат в главное меню

    await callback.message.answer(
        "📝 <b>Добавление нового атлета</b>\n\n"
        "Введите <b>Имя и Фамилия</b> ученика:",
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )
    await callback.answer()


@router.message(AdminManualAdd.waiting_for_name)
async def manual_add_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(AdminManualAdd.waiting_for_lessons)
    await message.answer(f"✅ Имя: <b>{message.text}</b>\n\nВведите количество занятий (0 для безлимита):",
                         parse_mode="HTML")


@router.message(AdminManualAdd.waiting_for_lessons)
async def manual_add_finish(
        message: types.Message,
        state: FSMContext,
        club: Club,  # <--- Исправил (объект из мидлвари)
        club_settings: dict,  # <--- Добавил (нужно для сроков)
        session: AsyncSession
):
    data = await state.get_data()
    name = data.get("name")

    # 1. Валидация ввода
    try:
        lessons = int(message.text)
    except ValueError:
        return await message.answer("❌ Введите числовое значение!")

    try:
        days = club_settings.get("limits", {}).get("subscription_days", 30)
        new_expire = datetime.now() + timedelta(days=days)
        new_student = Student(
            name=name,
            club_id=club.id,  # <--- Используем ID из объекта
            balance_lessons=999 if lessons == 0 else lessons,
            expire_date=new_expire,
            can_freeze=1,
            is_frozen=0
        )

        session.add(new_student)
        await session.commit()
        await session.refresh(new_student)

        # 4. Красивый ответ
        await message.answer(
            f"✅ <b>Атлет успешно добавлен!</b>\n\n"
            f"👤 Имя: <b>{name}</b>\n"
            f"📊 Баланс: <b>{new_student.balance_lessons} зан.</b>\n"
            f"⏳ Срок до: <b>{new_expire.strftime('%d.%m.%Y')}</b>\n"
            f"🆔 ID: <code>{new_student.id}</code>",
            parse_mode="HTML"
        )

        logger.info(f"🆕 [Клуб {club.id}] Админ вручную добавил атлета {name}")
        await state.clear()

    except Exception as e:
        await session.rollback()
        logger.error(f"❌ Ошибка ручного добавления в клубе {club.id}: {e}")
        await message.answer("❌ Ошибка при сохранении в базу.")


@router.message(AdminManualAdd.waiting_for_phone)
async def manual_add_process_phone(
        message: types.Message,
        state: FSMContext,
        club: Club,  # <--- ИСПРАВИЛ (берем объект из Middleware)
        session: AsyncSession
):
    # 1. Чистим номер телефона
    phone = "".join(filter(str.isdigit, message.text))
    if len(phone) < 10:
        return await message.answer("❌ Номер слишком короткий. Введите минимум 10 цифр:")

    # 2. Достаем данные из стейта (проверь ключ, в прошлом шаге был 'name')
    data = await state.get_data()
    student_name = data.get('name') or data.get('student_name') or 'Атлет'

    try:
        # 3. Создаем атлета и ЖЕСТКО привязываем к club.id
        new_student = Student(
            name=student_name,
            club_id=club.id,  # <--- Используем ID из объекта
            parent_phone=phone,
            parent_id=None,  # Пока не привязан к аккаунту ТГ
            expire_date=None,
            balance_lessons=0,
            can_freeze=1,
            is_frozen=0
        )

        session.add(new_student)
        await session.commit()
        await session.refresh(new_student)

        student_id = new_student.id

        # 4. Готовим кнопки
        builder = InlineKeyboardBuilder()
        # Важно: если в confirm_cash ты планируешь зачислять стандартный абонемент,
        # убедись, что хендлер этого калбэка знает, сколько занятий зачислять.
        builder.row(types.InlineKeyboardButton(
            text="💵 Оплатил наличными",
            callback_data=f"confirm_cash_{student_id}")
        )
        builder.row(types.InlineKeyboardButton(text="🏠 В меню", callback_data="admin"))

        await message.answer(
            f"✅ Атлет <b>{student_name}</b> добавлен в базу клуба <b>{club.name}</b>!\n"
            f"🆔 ID: <code>{student_id}</code>\n"
            f"📱 Телефон: <code>{phone}</code>\n\n"
            f"Хотите сразу активировать абонемент?",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )

        await state.clear()
        logger.success(f"🆕 [Клуб {club.id}] Админ добавил атлета {student_name}")

    except Exception as e:
        await session.rollback()
        logger.error(f"❌ Ошибка ручного добавления (Клуб {club.id}): {e}")
        await message.answer("❌ Ошибка при сохранении в базу данных.")


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
        club: Club,  # <--- ИСПРАВИЛ (берем объект из Middleware)
        club_settings: dict
):
    student_id = int(callback.data.split("_")[-1])
    now = datetime.now()

    try:
        # 1. Загружаем студента
        student = await session.get(Student, student_id)

        # 2. 🛡️ ПРОВЕРКА: Существует ли и принадлежит ли ЭТОМУ клубу?
        if not student or student.club_id != club.id:
            return await callback.answer("❌ Ошибка: атлет не найден в вашем клубе!", show_alert=True)

        # 3. Логика разморозки
        msg_unfreeze = ""
        if student.is_frozen == 1:
            student.is_frozen = 0
            msg_unfreeze = "\n❄️ <b>Абонемент разморожен!</b>"

        # 4. Логика списания занятий
        balance = student.balance_lessons or 0

        if balance >= 900:  # Режим безлимита
            usage_info = "♾ Режим: <b>Безлимит</b>"
        elif balance > 0:
            student.balance_lessons -= 1
            usage_info = f"📉 Осталось занятий: <b>{student.balance_lessons}</b>"
        else:
            # Если занятий 0
            return await callback.message.answer(
                f"🔴 <b>ДОСТУП ЗАПРЕЩЕН</b>\n👤 {student.name}\n❌ Занятия закончились! Нужно продлить.",
                parse_mode="HTML"
            )

        # 5. Обновляем время визита и сохраняем
        student.last_visit = now
        await session.commit()

        # 6. Уведомление родителю (уйдет от нужного бота автоматически)
        if student.parent_id:
            try:
                await callback.bot.send_message(
                    chat_id=student.parent_id,
                    text=f"🔔 <b>Вход зафиксирован:</b> {student.name}\n{usage_info}\nПриятной тренировки! 💪",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.warning(f"Не удалось уведомить родителя {student.parent_id}: {e}")

        # 7. Ответ админу
        await callback.message.edit_text(
            f"✅ <b>Вход отмечен вручную</b>\n👤 Атлет: <b>{student.name}</b>\n{usage_info}{msg_unfreeze}",
            parse_mode="HTML"
        )

        await callback.answer("Посещение зафиксировано")
        await state.clear()

    except Exception as e:
        await session.rollback()
        logger.error(f"❌ Ошибка ручного чекина (Клуб {club.id}, Student {student_id}): {e}")
        await callback.answer("⚠️ Ошибка сохранения", show_alert=True)


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
    # 1. Достаем нужные данные прямо из объекта club (который пришел из мидлвари)
    # Это гарантирует, что мы не получим ошибку отсутствия аргумента
    club_id = club.id
    current_settings = dict(club.club_settings)  # Делаем копию для безопасности

    new_info = message.text.strip()

    if len(new_info) > 200:
        return await message.answer("❌ Слишком длинный текст. Напишите короче.")

    # 2. Обновляем JSON
    if 'ui' not in current_settings:
        current_settings['ui'] = {}
    current_settings['ui']["payment_info"] = new_info

    # Присваиваем обратно, чтобы SQLAlchemy увидела изменения
    club.club_settings = current_settings

    # 3. Сохраняем в БД и чистим кэш
    await session.commit()
    cache_key = f"club_config:{message.bot.token}"
    await redis.delete(cache_key)
    kb = admin_keyboard(club_settings=current_settings, club_id=club_id)

    await message.answer(
        f"✅ <b>Реквизиты обновлены!</b>\n\nТекущий текст:\n<code>{new_info}</code>",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await state.clear()
