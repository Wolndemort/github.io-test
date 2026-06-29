from sqlalchemy.ext.asyncio import AsyncSession
from handlers.skud import trigger_dingtian_turnstile, save_and_test_turnstile
from sqlalchemy.orm.attributes import flag_modified
from handlers.states import AdminStates, AdminSettings, TurnstileSetup, AdminTariffStates
from redis.asyncio import Redis
from sqlalchemy import update
import pandas as pd
import os
from aiogram.types import FSInputFile
import asyncio
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database.db import get_all_users_count, get_active_subs_count, User, get_daily_stats, Student, \
    Club
from sqlalchemy import select
from handlers.buttons import admin_keyboard
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
        club: Club,
        club_settings: dict,
        is_owner: bool,
        is_super_admin: bool,
        session: AsyncSession
):
    if not (is_owner or is_super_admin):
        return

    message = event.message if isinstance(event, types.CallbackQuery) else event
    if isinstance(event, types.CallbackQuery):
        await event.answer()

    try:
        all_users = await get_all_users_count(club_id=club.id, session=session)
        active_subs = await get_active_subs_count(club_id=club.id, session=session)
        club_name = club_settings.get("ui", {}).get("club_name") or club.name

        # --- РАСЧЕТ ОСТАТКА ПОДПИСКИ ---
        sub_end = club.subscription_expire_at
        if sub_end:
            days_left = (sub_end - datetime.now()).days
            sub_info = f"<code>до {sub_end.strftime('%d.%m.%Y')} ({max(0, days_left)} дн.)</code>"
        else:
            sub_info = "<code>не активна</code>"
        # ------------------------------

        text = (
            f"📈 <b>Панель управления: {club_name}</b>\n\n"
            f"🔐 Подписка CRM: {sub_info}\n"  # Выводим в текст
            f"👥 Всего пользователей: <code>{all_users}</code>\n"
            f"💳 Активных абонементов: <code>{active_subs}</code>\n\n"
            "Чего желаете, босс?"
        )

        # Передаем дату в твою обновленную клавиатуру
        await message.answer(
            text,
            reply_markup=admin_keyboard(
                club_id=club.id,
                club_settings=club_settings,
                subscription_date=sub_end
            )
        )

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
        text="💰 Настройка тарифов",
        callback_data="admin_tariffs_sections"
    ))

    builder.row(types.InlineKeyboardButton(
        text="💳 Изменить реквизиты",
        callback_data="admin_edit_payments")
    )
    turnstile_config = club_settings.get("turnstile", {})
    t_status = "✅" if turnstile_config.get("enabled", False) else "❌"
    builder.row(types.InlineKeyboardButton(
                    text=f"{t_status} СКУД(Турникет)", callback_data='admin_turnstile_main'))
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
        # Явно говорим SQLAlchemy, что JSON-поле внутри изменилось
        flag_modified(club, "settings")

        try:
            session.add(club)
            await session.commit()
            await callback.answer("🔒 Интеграция СКУД успешно отключена", show_alert=True)
        except Exception as e:
            logger.error(f"Ошибка при отключении СКУД в БД: {e}")
            await callback.answer("❌ Не удалось сохранить изменения в БД", show_alert=True)
            return

        # ИСПРАВЛЕНО: Вызываем функцию главного меню СКУД, которую вы присылали в начале,
        # и передаем ей обновленный словарь настроек
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
    """Генерирует актуальное меню тарифов конкретной дисциплины после любых изменений"""
    discipline = club_settings.get("disciplines", {}).get(disc_id, {})
    tariffs = discipline.get("tariffs", [])
    d_type = discipline.get("type", "lessons")

    builder = InlineKeyboardBuilder()
    for idx, tariff in enumerate(tariffs):
        if d_type == "unlimited":
            t_text = f"💳 {tariff.get('days')} дн. — {tariff.get('price')} руб."
        else:
            t_text = f"💳 {tariff.get('count')} зан. / {tariff.get('days')} дн. — {tariff.get('price')} руб."
        builder.row(types.InlineKeyboardButton(text=t_text, callback_data=f"adm_tar_edit_{disc_id}_{idx}"))

    builder.row(types.InlineKeyboardButton(text="➕ Добавить тариф", callback_data=f"adm_tar_add_{disc_id}"))
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_tariffs_sections"))

    await message.answer(
        f"🥋 <b>Секция: {discipline.get('name')}</b>\n"
        f"Режим работы: <u>{'Безлимит ♾' if d_type == 'unlimited' else 'Лимитированные занятия 🔢'}</u>\n\n"
        f"Выберите тариф для управления или нажмите кнопку добавления:",
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
    disc_id = callback.data.split("_")[-1]
    discipline = club_settings.get("disciplines", {}).get(disc_id)
    if not discipline:
        return await callback.answer("Указанная секция не найдена!", show_alert=True)

    builder = InlineKeyboardBuilder()
    d_type = discipline.get("type", "lessons")

    type_label = "Безлимитная (♾)" if d_type == "unlimited" else "По занятиям (🔢)"
    builder.row(
        types.InlineKeyboardButton(text=f"🔄 Тип секции: {type_label}", callback_data=f"adm_tar_toggle_{disc_id}"))

    tariffs = discipline.get("tariffs", [])

    # Генерируем кнопки, только если тарифы есть
    for idx, tariff in enumerate(tariffs):
        t_text = f"💳 {tariff.get('days')} дн. — {tariff.get('price')} руб." if d_type == "unlimited" else f"💳 {tariff.get('count')} зан. / {tariff.get('days')} дн. — {tariff.get('price')} руб."
        builder.row(types.InlineKeyboardButton(text=t_text, callback_data=f"adm_tar_edit_{disc_id}_{idx}"))

    builder.row(types.InlineKeyboardButton(text="➕ Добавить тариф", callback_data=f"adm_tar_add_{disc_id}"))
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_tariffs_sections"))

    # ДИНАМИЧЕСКИЙ ТЕКСТ ПОДСКАЗКИ
    if not tariffs:
        tariffs_info = "⚠️ <b>Ни одного тарифного плана еще не создано!</b>\nНажмите кнопку ниже, чтобы добавить первый тариф."
    else:
        tariffs_info = "Управление существующей тарифной сеткой:"

    await callback.message.edit_text(
        f"🥋 <b>Направление: {discipline.get('name')}</b>\n"
        f"Текущий режим: <u>{'Безлимитные абонементы' if d_type == 'unlimited' else 'Списание занятий'}</u>\n\n"
        f"{tariffs_info}",
        reply_markup=builder.as_markup(), parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("adm_tar_toggle_"))
async def admin_toggle_section_type(
        callback: types.CallbackQuery,
        club_settings: dict,
        session,
        redis: Redis,
        bot,
        club_id: int
):
    disc_id = callback.data.split("_")[-1]

    if disc_id in club_settings["disciplines"]:
        cur = club_settings["disciplines"][disc_id].get("type", "lessons")
        new_type = "unlimited" if cur == "lessons" else "lessons"

        # 1. Меняем тип локально в словаре
        club_settings["disciplines"][disc_id]["type"] = new_type

        # Если переключили в безлимит — принудительно ставим маркер 999 во все существующие тарифы
        if new_type == "unlimited":
            for t in club_settings["disciplines"][disc_id].get("tariffs", []):
                t["count"] = 999

        # 2. Пишем изменения в БД и чистим Redis
        await save_club_settings(session, redis, bot.token, club_id, club_settings)
        await callback.answer("Тип направления изменен! ✨")

        # ================= ИСПРАВЛЕНИЕ ТУТ =================
        # Принудительно вызываем хендлер отрисовки меню этой же секции.
        # Передаем уже МОДИФИЦИРОВАННЫЙ club_settings, чтобы бот сразу прочитал новые данные!
        await admin_manage_section_tariffs(callback, club_settings)


@router.callback_query(F.data.startswith("adm_tar_edit_"))
async def admin_edit_tariff_menu(callback: types.CallbackQuery, club_settings: dict):
    """Экран изменения конкретного выбранного тарифа"""
    _, _, _, disc_id, tariff_idx = callback.data.split("_")
    tariff_idx = int(tariff_idx)
    discipline = club_settings["disciplines"][disc_id]
    tariff = discipline["tariffs"][tariff_idx]

    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text=f"💰 Цена: {tariff['price']} руб.", callback_data=f"input_tar_price_{disc_id}_{tariff_idx}"))
    builder.row(types.InlineKeyboardButton(text=f"⏳ Срок: {tariff['days']} дней", callback_data=f"input_tar_days_{disc_id}_{tariff_idx}"))
    if discipline.get("type") == "lessons":
        builder.row(types.InlineKeyboardButton(text=f"🔢 Занятий: {tariff['count']}", callback_data=f"input_tar_count_{disc_id}_{tariff_idx}"))

    builder.row(types.InlineKeyboardButton(text="❌ Удалить тариф", callback_data=f"adm_tar_del_{disc_id}_{tariff_idx}"))
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"adm_tar_sect_{disc_id}"))

    await callback.message.edit_text(
        f"⚙️ <b>Редактирование тарифа ({discipline['name']})</b>\n\nВы можете изменить отдельные параметры или полностью удалить тариф:",
        reply_markup=builder.as_markup(), parse_mode="HTML"
    )



@router.callback_query(F.data.startswith("adm_tar_del_"))
async def admin_delete_tariff(callback: types.CallbackQuery, club_settings: dict, session, redis: Redis, bot, club_id: int):
    """Удаление тарифа из списка"""
    _, _, _, disc_id, tariff_idx = callback.data.split("_")
    tariffs = club_settings["disciplines"].get(disc_id, {}).get("tariffs", [])
    if 0 <= int(tariff_idx) < len(tariffs):
        tariffs.pop(int(tariff_idx))
        await save_club_settings(session, redis, bot.token, club_id, club_settings)
        await callback.answer("Тариф удален!")
    callback.data = f"adm_tar_sect_{disc_id}"
    await admin_manage_section_tariffs(callback, club_settings)


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
        await callback.message.answer("🔢 Введите новое <b>количество занятий</b> для лимита:", parse_mode="HTML")
    await callback.answer()


@router.message(AdminTariffStates.waiting_for_price)
@router.message(AdminTariffStates.waiting_for_days)
@router.message(AdminTariffStates.waiting_for_count)
async def admin_save_tariff_field(message: types.Message, state: FSMContext, club_settings: dict, session, redis: Redis,
                                  bot):
    """Валидация и сохранение измененного текстового поля"""
    if not message.text.isdigit():
        return await message.answer("❌ Ошибка ввода! Пожалуйста, отправьте корректное целое число.")

    val = int(message.text)
    s_data = await state.get_data()
    disc_id, idx, field = s_data["disc_id"], s_data["tariff_idx"], s_data["edit_type"]

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
@router.message(AdminTariffStates.add_days)
async def admin_add_tariff_days(
        message: types.Message,
        state: FSMContext,
        club_settings: dict,
        session,
        redis: Redis,
        bot
):
    if not message.text.isdigit():
        return await message.answer("❌ Срок действия должен быть числом дней! Попробуйте еще раз:")

    days = int(message.text)
    s_data = await state.get_data()
    disc_id = s_data["disc_id"]
    club_id = s_data["club_id"]
    d_type = s_data["d_type"]
    new_price = s_data["new_price"]

    # ЕСЛИ СЕКЦИЯ БЕЗЛИМИТНАЯ — СТАВИМ COUNT = 999 И СРАЗУ СОХРАНЯЕМ В БД
    if d_type == "unlimited":
        new_tariff = {
            "count": 999,  # Маркер безлимита для вашей системы
            "price": new_price,
            "days": days
        }

        if "tariffs" not in club_settings["disciplines"][disc_id]:
            club_settings["disciplines"][disc_id]["tariffs"] = []

        club_settings["disciplines"][disc_id]["tariffs"].append(new_tariff)

        await save_club_settings(session, redis, bot.token, club_id, club_settings)
        await state.clear()
        await return_to_tariff_menu(message, club_settings, disc_id)

    # ЕСЛИ СЕКЦИЯ ОБЫЧНАЯ — ПЕРЕХОДИМ К ВВОДУ ЗАНЯТИЙ
    else:
        await state.update_data(new_days=days)
        await state.set_state(AdminTariffStates.add_count)

        await message.answer(
            "<b>Шаг 3 из 3:</b> Введите лимит количества занятий для этого тарифа (например: 12):",
            parse_mode="HTML"
        )


# 4. Ловим ввод ЗАНЯТИЙ (Только для обычных секций)
@router.message(AdminTariffStates.add_count)
async def admin_add_tariff_count(
        message: types.Message,
        state: FSMContext,
        club_settings: dict,
        session,
        redis: Redis,
        bot
):
    if not message.text.isdigit():
        return await message.answer("❌ Количество занятий должно быть целым числом! Попробуйте еще раз:")

    count = int(message.text)
    s_data = await state.get_data()
    disc_id = s_data["disc_id"]
    club_id = s_data["club_id"]

    new_tariff = {
        "count": count,
        "price": s_data["new_price"],
        "days": s_data["new_days"]
    }

    if "tariffs" not in club_settings["disciplines"][disc_id]:
        club_settings["disciplines"][disc_id]["tariffs"] = []

    club_settings["disciplines"][disc_id]["tariffs"].append(new_tariff)

    await save_club_settings(session, redis, bot.token, club_id, club_settings)
    await state.clear()
    await return_to_tariff_menu(message, club_settings, disc_id)
