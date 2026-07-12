from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.filters import StateFilter
import copy
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm.attributes import flag_modified
from handlers.skud import save_and_test_turnstile, trigger_dingtian_turnstile
from handlers.states import AdminStates, AdminSettings, TurnstileSetup, AdminTariffStates, AdminScheduleStates, \
    YooKassaSetupStates, AdminSettingsSG
from redis.asyncio import Redis
from sqlalchemy import update
import pandas as pd
import os
from handlers.buttons import get_scanner_keyboard
from aiogram.types import FSInputFile
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
import asyncio
from loguru import logger


router = Router()

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
    # 1. Жесткая SaaS-проверка прав доступа
    if not (is_owner or is_super_admin):
        return

    # Извлекаем message в зависимости от типа события (текст или нажатие кнопки)
    message = event.message if isinstance(event, types.CallbackQuery) else event

    # Отжимаем крутилку на инлайн-кнопке моментально
    if isinstance(event, types.CallbackQuery):
        await event.answer()

    try:
        # 2. Агрегируем свежую оперативную статистику из базы данных
        all_users = await get_all_users_count(club_id=club.id, session=session)
        active_subs = await get_active_subs_count(club_id=club.id, session=session)
        club_name = club_settings.get("ui", {}).get("club_name") or club.name

        # --- РАСЧЕТ ОСТАТКА ПОДПИСКИ CRM ---
        sub_end = club.subscription_expire_at
        if sub_end:
            # Убираем таймзону для честного вычитания дат naive datetime на Аэзе
            days_left = (sub_end.replace(tzinfo=None) - datetime.now().replace(tzinfo=None)).days
            sub_info = f"<code>до {sub_end.strftime('%d.%m.%Y')} ({max(0, days_left)} дн.)</code>"
        else:
            sub_info = "<code>не активна</code>"
        # ----------------------------------

        # 3. Формируем красивый итоговый текст для босса
        text = (
            f"📈 <b>Панель управления: {club_name}</b>\n\n"
            f"🔐 Подписка CRM: {sub_info}\n"
            f"👥 Всего пользователей: <code>{all_users}</code>\n"
            f"💳 Активных абонементов: <code>{active_subs}</code>\n\n"
            "Чего желаете, босс?"
        )

        # 4. Отправляем инлайн-меню с настройками и статистикой
        await message.answer(
            text=text,
            reply_markup=admin_keyboard(
                club_settings=club_settings,  # Первым — словарь настроек
                club_id=club.id,  # Вторым — числовой ID клуба
                subscription_date=sub_end  # Третьим — дата окончания подписки
            ),
            parse_mode="HTML"
        )

        # 5. ФИКС: Выкатываем нативную нижнюю панель СКУД-сканера, откуда сработает sendData
        await message.answer(
            text="📸 Нативная панель СКУД активирована внизу экрана.",
            reply_markup=get_scanner_keyboard(club_id=club.id)
        )

    except Exception as e:
        logger.error(f"❌ Критическая ошибка в админ-панели клуба {club.id}: {e}", exc_info=True)


@router.callback_query(F.data == "admin_keyboard")
async def back_to_admin_main_menu(
        callback: types.CallbackQuery,
        club_settings: dict,
        club: Club,  # Объект из мидлвари
        is_owner: bool,
        is_super_admin: bool
):
    # Жесткая SaaS-проверка прав
    if not (is_owner or is_super_admin):
        await callback.answer("У вас нет доступа!", show_alert=True)
        return

    await callback.answer()  # Сразу гасим часики на кнопке

    club_name = club_settings.get("ui", {}).get("club_name") or club.name

    # 1. Изменяем старое сообщение — возвращаем инлайн-панель управления
    await callback.message.edit_text(
        text=f"🏠 <b>Панель управления: {club_name}</b>\nВыберите нужный раздел:",
        reply_markup=admin_keyboard(
            club_settings=club_settings,
            club_id=club.id,
            subscription_date=club.subscription_expire_at
        ),
        parse_mode="HTML"
    )

    # 2. ФИКС: Отправляем новое сообщение, которое выкатит нативную кнопку сканера снизу
    await callback.message.answer(
        text="📸 Панель СКУД активирована внизу экрана.",
        reply_markup=get_scanner_keyboard(club_id=club.id)
    )


@router.callback_query(F.data == "admin_settings")
async def admin_settings_menu(callback: types.CallbackQuery, club_settings: dict):
    builder = InlineKeyboardBuilder()
    features = club_settings.get("features", {})
    limits = club_settings.get("limits", {})

    # Список системных кнопок переключателей модулей
    buttons = {
        "freeze": "Заморозка",
        "qr_checkin": "QR-вход",
        "manual_add": "Ручное добавление",
        "online_payments": "Онлайн-платежи"
    }

    for key, label in buttons.items():
        status = "✅" if features.get(key, False) else "❌"
        builder.row(types.InlineKeyboardButton(
            text=f"{status} {label}",
            callback_data=f"toggle_feat_{key}")
        )

    # Если онлайн-платежи включены, показываем кнопку для настройки ключей ЮKassa
    if features.get("online_payments", False):
        builder.row(types.InlineKeyboardButton(
            text="🔑 Настройка ключей ЮKassa",
            callback_data="admin_setup_yookassa"
        ))

    # ⚙️ НАША НОВАЯ КНОПКА: Переход в меню изменения сессий СКУД и шага заморозок
    builder.row(types.InlineKeyboardButton(
        text="⚙️ Настройка лимитов клуба",
        callback_data="manage_club_limits"  # 👈 Тот самый колбэк, который ведёт на новое меню!
    ))

    builder.row(types.InlineKeyboardButton(
        text="💰 Настройка тарифов",
        callback_data="admin_tariffs_sections"
    ))

    builder.row(types.InlineKeyboardButton(
        text="💳 Изменить реквизиты",
        callback_data="admin_edit_payments"
    ))

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
        is_owner: bool,
        is_super_admin: bool
):
    if not (is_owner or is_super_admin):
        return await callback.answer("❌ У вас нет прав администратора.", show_alert=True)

    await state.set_state(AdminManualAdd.waiting_for_name)

    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Отмена", callback_data="admin")

    await callback.message.answer(
        "📝 <b>Добавление нового атлета</b>\n\n"
        "Введите <b>Имя и Фамилия</b> ученика:",
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )
    await callback.answer()


@router.message(AdminManualAdd.waiting_for_name)
async def manual_add_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(AdminManualAdd.waiting_for_phone)
    await message.answer(
        f"✅ Имя: <b>{message.text}</b>\n\n"
        f"Введите <b>номер телефона</b> родителя (например, 79991112233):",
        parse_mode="HTML"
    )


# ШАГ 3: Поймали телефон -> Запрашиваем ДЕНЬ РОЖДЕНИЯ
@router.message(AdminManualAdd.waiting_for_phone)
async def manual_add_process_phone(message: types.Message, state: FSMContext):
    phone = "".join(filter(str.isdigit, message.text))
    if len(phone) < 10:
        return await message.answer("❌ Номер слишком короткий. Введите минимум 10 цифр:")

    await state.update_data(phone=phone)
    await state.set_state(AdminManualAdd.waiting_for_birthday)
    await message.answer(
        f"✅ Телефон: <code>{phone}</code>\n\n"
        f"Введите <b>день рождения атлета</b> в формате ДД.ММ.ГГГГ\n"
        f"<i>(например: 25.10.2015 или напишите '0', если неизвестно):</i>",
        parse_mode="HTML"
    )


# ШАГ 4: Поймали ДР -> Генерируем кнопки доступных ТАРИФОВ
@router.message(AdminManualAdd.waiting_for_birthday)
async def manual_add_process_birthday(
        message: types.Message,
        state: FSMContext,
        club_settings: dict
):
    if message.text.strip() == "0":
        await state.update_data(birthday=None)
    else:
        try:
            birthday_date = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
            await state.update_data(birthday=birthday_date.isoformat())
        except ValueError:
            return await message.answer("❌ Неверный формат! Введите дату строго как ДД.ММ.ГГГГ (например, 15.08.2012):")

    # Вытаскиваем все тарифы из настроек клуба, разбитые по дисциплинам
    disciplines = club_settings.get("disciplines", {})
    kb = InlineKeyboardBuilder()

    tariff_found = False
    for disc_code, disc_data in disciplines.items():
        disc_name = disc_data.get("name", disc_code)
        tariffs = disc_data.get("tariffs", [])

        if tariffs:
            # Делаем заголовок секции, если тарифов много
            kb.row(types.InlineKeyboardButton(text=f"🔹 {disc_name} 🔹", callback_data="ignore"))

            for idx, tariff in enumerate(tariffs):
                count = tariff.get("count", 0)
                days = tariff.get("days", 30)
                price = tariff.get("price", 0)

                # Формируем понятный текст кнопки
                if count == 999:
                    label = f"Безлимит ({days} дн.) — {price}₽"
                else:
                    label = f"{count} зан. ({days} дн.) — {price}₽"

                # Зашиваем код дисциплины и индекс тарифа в callback_data
                kb.row(types.InlineKeyboardButton(
                    text=label,
                    callback_data=f"manual_select_tariff_{disc_code}_{idx}"
                ))
                tariff_found = True

    if not tariff_found:
        return await message.answer("⚠️ В настройках вашего клуба не найдено активных тарифов. Заведите их в админке!")

    kb.row(types.InlineKeyboardButton(text="❌ Отмена", callback_data="admin"))

    # Переключаем стейт на ожидание клика по кнопке
    await state.set_state(AdminManualAdd.waiting_for_lessons)
    await message.answer(
        "✅ Дата рождения сохранена!\n\n"
        "Выберите один из <b>действующих тарифов клуба</b> для автоматической активации абонемента:",
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )


# ШАГ 5: Поймали кнопку тарифа -> Создаем атлета со всеми лимитами в БД
@router.callback_query(AdminManualAdd.waiting_for_lessons, F.data.startswith("manual_select_tariff_"))
async def manual_add_finish(
        callback: types.CallbackQuery,
        state: FSMContext,
        club: Club,
        club_settings: dict,
        session: AsyncSession
):
    # Парсим callback_data, доставая код дисциплины и индекс тарифа
    parts = callback.data.split("_")
    disc_code = parts[3]
    tariff_idx = int(parts[4])

    disc_cfg = club_settings.get("disciplines", {}).get(disc_code, {})
    tariffs = disc_cfg.get("tariffs", [])

    if tariff_idx >= len(tariffs):
        return await callback.answer("Ошибка: тариф не найден ❌", show_alert=True)

    selected_tariff = tariffs[tariff_idx]
    count = selected_tariff.get("count", 0)
    days = selected_tariff.get("days", 30)
    price = selected_tariff.get("price", 0)

    data = await state.get_data()
    name = data.get("name")
    phone = data.get("phone")
    birthday_str = data.get("birthday")

    birthday_obj = None
    if birthday_str:
        birthday_obj = datetime.strptime(birthday_str, "%Y-%m-%d").date()

    try:
        new_expire = datetime.now() + timedelta(days=days)

        # Создаем студента с точными параметрами из конфига и last_visit
        new_student = Student(
            name=name,
            club_id=club.id,
            parent_phone=phone,
            birthday=birthday_obj,
            parent_id=None,
            balance_lessons=count,
            expire_date=new_expire,
            can_freeze=1,
            is_frozen=0,
            last_visit=datetime.now()
        )

        session.add(new_student)
        await session.commit()
        await session.refresh(new_student)

        # Формируем кнопку подтверждения оплаты для ручного ввода
        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(
            text="💵 Подтвердить оплату наличными",
            callback_data=f"confirm_manual_pay_{new_student.id}_{price}"
        ))
        builder.row(types.InlineKeyboardButton(text="🏠 В меню", callback_data="admin"))

        t_label = f"Безлимит ({days} дн.)" if count == 999 else f"{count} зан. ({days} дн.)"

        await callback.message.edit_text(
            f"✅ <b>Атлет успешно добавлен по тарифу!</b>\n\n"
            f"👤 Имя: <b>{name}</b>\n"
            f"🎂 ДР: <b>{birthday_obj.strftime('%d.%m.%Y') if birthday_obj else 'не указан'}</b>\n"
            f"📱 Телефон: <code>{phone}</code>\n"
            f"🥋 Направление: <b>{disc_cfg.get('name')}</b>\n"
            f"📊 Тариф: <b>{t_label}</b>\n"
            f"💰 К оплате: <b>{price} ₽</b>\n"
            f"⏳ Действует до: <b>{new_expire.strftime('%d.%m.%Y')}</b>\n"
            f"🆔 ID: <code>{new_student.id}</code>",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )

        logger.info(f"🆕 [Клуб {club.id}] Админ вручную добавил атлета {name} по тарифу {t_label}")
        await state.clear()

    except Exception as e:
        await session.rollback()
        logger.error(f"❌ Ошибка ручного сохранения атлета по тарифу: {e}")
        await callback.message.answer("❌ Ошибка при сохранении в базу данных.")

    await callback.answer()


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
        club: Club,
        club_settings: dict
):
    # 1. 🛡️ ЗАЩИТА ОТ ДВОЙНОГО КЛИКА (Проверка интерфейса)
    if any(word in (callback.message.text or "") for word in
           ["✅ ВХОД ОТМЕЧЕН", "🔴 ДОСТУП ЗАПРЕЩЕН", "Вход отмечен вручную"]):
        return await callback.answer("Этот запрос уже обработан! ⚠️", show_alert=True)

    student_id = int(callback.data.split("_")[-1])

    # Работаем строго в наивном формате UTC (как в базе данных на Аэзе) для защиты от TypeError
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        # 2. Загружаем студента из базы с row-level блокировкой (with_for_update) от Race Condition
        student_query = (
            select(Student)
            .where(Student.id == student_id)
            .with_for_update()
        )
        student_res = await session.execute(student_query)
        student = student_res.scalar_one_or_none()

        # 3. ПРОВЕРКА: Существует ли и принадлежит ли ЭТОМУ клубу?
        if not student or student.club_id != club.id:
            return await callback.answer("❌ Ошибка: атлет не найден в вашем клубе!", show_alert=True)

        student_name = str(student.name)

        # 🛑 АНТИ-ФРОД / АНТИ-СПАМ (Защищает реле и базу от бешеного флуда кликов админа)
        if student.last_visit:
            last_visit_naive = student.last_visit.replace(tzinfo=None)
            if (now_naive - last_visit_naive).total_seconds() < 10:
                return await callback.answer("⏳ Не спамьте, турникет уже обрабатывает предыдущий запрос.",
                                             show_alert=True)

        # === 4. ЛОГИКА ДОСРОЧНОЙ РАЗМОРОЗКИ С КОМПЕНСАЦИЕЙ ДНЕЙ ===
        msg_unfreeze = ""
        is_was_frozen = False
        returned_early_days = 0

        if student.is_frozen and student.frozen_at:
            frozen_at_naive = student.frozen_at.replace(tzinfo=None)

            # Сколько чистых дней атлет РЕАЛЬНО пробыл в заморозке
            days_passed = (now_naive.date() - frozen_at_naive.date()).days
            days_passed = max(0, days_passed)

            # Получаем шаг заморозки из настроек лимитов клуба (дефолт 7)
            freeze_step = club_settings.get("limits", {}).get("freeze_days_step", 7)

            # Если чел пришел раньше, чем заложенный шаг заморозки — вычитаем разницу назад
            if days_passed < freeze_step:
                diff = freeze_step - days_passed
                if student.expire_date:
                    student.expire_date -= timedelta(days=diff)
                returned_early_days = diff
                logger.info(f"❄️ Админ Досрочный выход: {student_name} недогулял {diff} дн. Срок уменьшен назад.")
            else:
                logger.info(f"❄️ Полноценный выход через админку: {student_name} перегулял лимит {freeze_step} дн.")

            # Снимаем флаги заморозки
            student.is_frozen = 0
            student.frozen_at = None
            is_was_frozen = True
            await session.flush()

        # === 5. КОНТРОЛЬ СЕССИИ (Таймаут прохода из JSONB настроек) ===
        limits = club_settings.get("limits", {})
        timeout_mins = limits.get("session_timeout_minutes", 150)

        is_inside_session = False
        if student.last_visit:
            last_visit_naive = student.last_visit.replace(tzinfo=None)
            if (now_naive - last_visit_naive).total_seconds() < (timeout_mins * 60):
                is_inside_session = True

        # 6. Проверка права доступа (Срок действия абонемента — уже обновленный после разморозки)
        expire_naive = student.expire_date.replace(tzinfo=None) if student.expire_date else None
        if not expire_naive or expire_naive < now_naive:
            await callback.message.edit_text(
                f"🔴 <b>ДОСТУП ЗАПРЕЩЕН</b>\n👤 Атлет: <b>{student_name}</b>\n❌ Срок действия абонемента истек!",
                parse_mode="HTML"
            )
            await state.clear()
            return await callback.answer("Срок действия абонемента истек! ❌", show_alert=True)

        # Проверяем лимиты занятий (маркер 999 — безлимит)
        balance = student.balance_lessons or 0
        is_unlimited = (balance == 999)

        if not is_unlimited and not is_inside_session and balance <= 0:
            await callback.message.edit_text(
                f"🔴 <b>ДОСТУП ЗАПРЕЩЕН</b>\n👤 Атлет: <b>{student_name}</b>\n❌ Занятия закончились! Нужно продлить абонемент.",
                parse_mode="HTML"
            )
            await state.clear()
            return await callback.answer("У атлета закончились занятия! ❌", show_alert=True)

        # === 7. ПРИМЕНЯЕМ ЛОГИКУ СПИСАНИЯ ЗАНЯТИЙ ===
        usage_info = ""
        parent_text = ""

        if is_unlimited:
            usage_info = "\n♾ Режим: <b>Безлимит</b>"
            parent_text = "♾ Режим: <b>Безлимит</b>"
        elif is_inside_session:
            # Повторный визит — занятие НЕ списываем, время визита НЕ обновляем
            session_end = student.last_visit + timedelta(minutes=timeout_mins)
            session_end_str = session_end.strftime("%H:%M")
            usage_info = f"\n🔄 <b>Повторный визит сессии ({timeout_mins} мин).</b> Занятие сохранено.\n📊 Баланс: <b>{balance} зан.</b>"
            parent_text = f"🔄 <b>Повторный вход в зал (в рамках сессии).</b>\n📊 Баланс: {balance} зан."
        else:
            # Обычный новый визит — списываем занятие и открываем новую сессию
            student.balance_lessons -= 1
            student.last_visit = now_naive  # Фиксируем время новой сессии
            usage_info = f"\n📉 Списано 1 занятие.\n📦 Осталось: <b>{student.balance_lessons} зан.</b>"
            parent_text = f"📉 Списано 1 занятие.\n📦 Осталось: {student.balance_lessons} зан."

        # === 8. ФИКСИРУЕМ ИЗМЕНЕНИЯ В БАЗЕ (Освобождаем row-level блокировку ДО сетевого запроса к реле) ===
        try:
            await session.commit()
        except Exception as db_err:
            logger.error(f"Ошибка коммита базы данных перед СКУД (Админ ручной чекин): {db_err}")
            return await callback.answer("❌ Ошибка сохранения данных визита в БД.", show_alert=True)

        # === 9. ИНТЕГРАЦИЯ ТУРНИКЕТА (БЕЗ блокировки транзакции базы данных) ===
        turnstile_config = club_settings.get("turnstile", {})
        turnstile_status = ""
        status_emoji = "🟢"

        if turnstile_config.get("enabled", False):
            try:
                base_url = str(turnstile_config.get("base_url", ""))
                if base_url and not base_url.startswith("http"):
                    turnstile_config["base_url"] = f"http://{base_url}"

                # Физический запрос к реле ДингТиан (база уже свободна)
                turnstile_opened = await trigger_dingtian_turnstile(turnstile_config)

                if turnstile_opened:
                    turnstile_status = "\n✅ <b>Турникет открыт</b>"
                else:
                    return await callback.answer("⚠️ Железо СКУД отклонило команду ручного открытия!", show_alert=True)

            except Exception as sku_err:
                logger.warning(f"Сбой сети СКУД при ручном чекине: {sku_err}. Пропускаем атлета в базе.")
                turnstile_status = "\n⚠️ <b>Микросбой сети турникета. Проход зафиксирован.</b>"
        else:
            turnstile_status = "\nℹ️ <i>СКУД отключен в настройках</i>"

        # Красивый текст уведомления о компенсации дней досрочной разморозки
        if is_was_frozen:
            if returned_early_days > 0:
                msg_unfreeze = f"\n❄️ <b>Досрочная разморозка!</b>\n⚠️ Сдвиг абонемента назад на <b>-{returned_early_days} дн.</b> за досрочный выход."
            else:
                msg_unfreeze = f"\n❄️ <b>Абонемент автоматически разморожен!</b>"

        # 10. КРАСИВОЕ SaaS-УВЕДОМЛЕНИЕ ДЛЯ КЛИЕНТА (РОДИТЕЛЯ)
        if student.parent_id:
            try:
                await callback.bot.send_message(
                    chat_id=int(student.parent_id),
                    text=f"🔔 <b>Вход зафиксирован:</b> {student_name}\n{parent_text}\nПриятной тренировки! 💪",
                    parse_mode="HTML"
                )
            except Exception as parent_err:
                logger.warning(f"Не удалось уведомить родителя {student.parent_id}: {parent_err}")

        # 11. UI: Меняем текст сообщения админу, фиксируя успешный чекин и убирая инлайн-кнопки
        expire_str = student.expire_date.strftime('%d.%m.%Y') if student.expire_date else "Не указано"
        await callback.message.edit_text(
            f"{status_emoji} <b>Вход отмечен вручную</b>\n👤 Атлет: <b>{student_name}</b>"
            f"{usage_info}"
            f"\n📅 Действует до: <b>{expire_str}</b>"
            f"{msg_unfreeze}"
            f"{turnstile_status}",
            parse_mode="HTML"
        )

        await callback.answer("Посещение зафиксировано")
        await state.clear()

    except Exception as e:
        await session.rollback()
        logger.error(f"❌ Критическая ошибка ручного чекина (Клуб {club.id}): {e}", exc_info=True)
        await callback.answer("⚠️ Критическая ошибка сохранения", show_alert=True)


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

        try:
            # ✅ ИСПРАВЛЕНО: Привязываем объект к сессии для выполнения UPDATE, а не INSERT
            db_club = await session.merge(club)

            # Явно говорим SQLAlchemy, что JSON-поле внутри привязанного объекта изменилось
            flag_modified(db_club, "settings")

            await session.commit()
            await callback.answer("🔒 Интеграция СКУД успешно отключена", show_alert=True)
        except Exception as e:
            logger.error(f"Ошибка при отключении СКУД в БД: {e}")
            await session.rollback()  # 🚨 Обязательно откатываем сессию при ошибке, чтобы СУБД не висла
            await callback.answer("❌ Не удалось сохранить изменения в БД", show_alert=True)
            return

        # Передаем обновленный словарь настроек в главное меню СКУД
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


from datetime import datetime  # Убедитесь, что импорт есть вверху файла


@router.callback_query(F.data.startswith("adm_tar_del_"))
async def admin_delete_tariff(
        callback: types.CallbackQuery,
        club_settings: dict,
        session,
        redis: Redis,
        bot,
        club_id: int
):
    """Удаление тарифа из списка с защитой от поломки базы"""
    _, _, _, disc_id, tariff_idx = callback.data.split("_")
    tariff_idx_int = int(tariff_idx)

    tariffs = club_settings["disciplines"].get(disc_id, {}).get("tariffs", [])

    if 0 <= tariff_idx_int < len(tariffs):
        target_tariff = tariffs[tariff_idx_int]
        count = target_tariff.get("count", 0)  # Количество уроков в тарифе (например, 8, 12 или 999)

        # ПРОВЕРКА В БД: Ищем активных спортсменов этого клуба, у которых совпадает баланс
        # и абонемент еще не истек (действует прямо сейчас)
        stmt = select(Student).where(
            Student.club_id == club_id,
            Student.balance_lessons == count,
            Student.expire_date > datetime.now()
        )
        result = await session.execute(stmt)
        active_students = result.scalars().all()

        # Если нашли хотя бы одного человека — жестко прерываем удаление и предупреждаем админа!
        if active_students:
            # Собираем первые три имени для красивого вывода в чат
            names = ", ".join([s.name for s in active_students[:3]])
            if len(active_students) > 3:
                names += " и др."

            return await callback.message.answer(
                f"❌ <b>Невозможно удалить тариф!</b>\n\n"
                f"Этот тарифный план сейчас активирован у действующих спортсменов клуба:\n"
                f"👤 <code>{names}</code>\n\n"
                f"Сначала дождитесь окончания их абонементов или измените их баланс вручную, "
                f"чтобы не нарушить работу CRM-системы.",
                parse_mode="HTML"
            )

        # Если активных людей по этому тарифу нет — спокойно удаляем
        tariffs.pop(tariff_idx_int)
        await save_club_settings(session, redis, bot.token, club_id, club_settings)
        await callback.answer("Тариф удален!")

    # Возвращаем админа в меню тарифов этой секции
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
    # === ДОБАВИЛИ ТОП-АКЦЕНТ НА БЕЗЛИМИТ СЮДА ===
    elif parts[2] == "count":
        await state.set_state(AdminTariffStates.waiting_for_count)
        text = (
            "🔢 <b>Введите новое количество занятий для тарифа:</b>\n\n"
            "• Укажите обычный лимит тренировок (например: <code>8</code>, <code>12</code>, <code>24</code>).\n\n"
            "🚨🚨🚨 <b>ВАЖНО: ЕСЛИ ВЫ ДЕЛАЕТЕ ЭТОТ ТАРИФ БЕЗЛИМИТНЫМ — ВВЕДИТЕ СТРОГО ЧИСЛО</b> <code>999</code> 🚨🚨🚨\n\n"
            "<i>Если ввести 999, система автоматически переключит проход по этому тарифу в режим безлимита без списания занятий!</i>"
        )
        await callback.message.answer(text=text, parse_mode="HTML")
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


# =====================================================================
# ШАГ 1: ВЫБОР СЕКЦИИ (Сохраняем club_id, чтобы не терялся)
# =====================================================================
@router.callback_query(F.data == "admin_schedule_main")
async def admin_schedule_select_discipline(callback: types.CallbackQuery, state: FSMContext, club: Club, club_settings: dict):
    disciplines = club_settings.get("disciplines", {})
    
    if not disciplines:
        return await callback.answer("❌ В конфиге клуба пока нет созданных дисциплин!", show_alert=True)
        
    # Сохраняем ID клуба в стейт сразу, чтобы удаление понимало, где чистить базу
    await state.update_data(club_id=club.id)
    
    builder = InlineKeyboardBuilder()
    
    for disc_id, disc_data in disciplines.items():
        builder.row(types.InlineKeyboardButton(
            text=f"🥋 {disc_data['name']}",
            callback_data=f"adm_sch_manage_{disc_id}"
        ))
        
    builder.row(types.InlineKeyboardButton(text="🔙 Назад в админку", callback_data="admin"))
    
    await callback.message.edit_text(
        text="📅 <b>Управление расписанием</b>\n\nВыберите спортивную дисциплину для настройки сетки занятий:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()


# =====================================================================
# ШАГ 1.5: ВЫБОР ДНЯ НЕДЕЛИ
# =====================================================================
@router.callback_query(F.data.startswith("adm_sch_manage_"))
async def admin_start_schedule_manage(callback: types.CallbackQuery, state: FSMContext, club_id: int, club_settings: dict):
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
    await callback.message.edit_text(
        text=f"📅 <b>Управление расписанием: {disc_name}</b>\n\nВыберите день недели, чтобы посмотреть текущие занятия или добавить новое:",
        reply_markup=days_kb,
        parse_mode="HTML"
    )
    await callback.answer()


from aiogram.filters import StateFilter


@router.callback_query(F.data.startswith("sch_day_"), StateFilter("*"))
async def admin_schedule_choose_day(
        callback: types.CallbackQuery,
        state: FSMContext,
        club_settings: dict,
        manual_day: str = None  # Принимаем день напрямую при удалении
):
    # Принудительно возвращаем админу рабочее состояние для этого экрана
    await state.set_state(AdminScheduleStates.choose_day)

    # Если день передан вручную, берем его. Если нет — парсим из callback.data
    day = manual_day if manual_day else callback.data.split("_")[-1]

    s_data = await state.get_data()
    disc_id = s_data.get("disc_id")

    if not disc_id:
        return await callback.answer("Ошибка контекста: выберите секцию заново ❌", show_alert=True)

    await state.update_data(chosen_day=day)

    day_names = {"mon": "Понедельник", "tue": "Вторник", "wed": "Среда", "thu": "Четверг", "fri": "Пятница",
                 "sat": "Суббота", "sun": "Воскресенье"}

    discipline_block = club_settings["disciplines"].get(disc_id, {})
    schedule_data = discipline_block.get("schedule", {})

    if not isinstance(schedule_data, dict) or day not in schedule_data:
        day_lessons = []
    else:
        day_lessons = schedule_data[day]

    builder = InlineKeyboardBuilder()
    text_lines = [f"📅 <b>Расписание на {day_names[day]}</b>\n"]

    if not day_lessons:
        text_lines.append("<i>Занятий пока нет.</i>")
    else:
        for idx, lesson in enumerate(day_lessons):
            text_lines.append(
                f"#{idx + 1} | ⏱ <b>{lesson['time']}</b> — {lesson['coach']} (👥 Мест: {lesson['max_slots']})")
            builder.row(types.InlineKeyboardButton(
                text=f"❌ Удалить #{idx + 1} ({lesson['time']})",
                callback_data=f"adm_sch_del_{disc_id}_{day}_{idx}"
            ))

    builder.row(types.InlineKeyboardButton(text="➕ Добавить занятие", callback_data="adm_sch_start_input_time"))
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад к дням", callback_data=f"adm_sch_manage_{disc_id}"))

    await callback.message.edit_text(
        text="\n".join(text_lines),
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()


# =====================================================================
# ХЕНДЛЕР УДАЛЕНИЯ (Исправленный: answer() вызывается сразу + фоновое сохранение)
# ====================================================================

# ХЕНДЛЕР УДАЛЕНИЯ (Идеально последовательный, без конфликта сессий)
# =====================================================================
@router.callback_query(F.data.startswith("adm_sch_del_"))
async def admin_delete_schedule_lesson(
        callback: types.CallbackQuery,
        state: FSMContext,
        club_settings: dict,
        session,
        redis: Redis,
        bot
):
    # Отвечаем Телеграму сразу
    await callback.answer("Удалено!")

    _, _, _, disc_id, day, lesson_idx = callback.data.split("_")
    lesson_idx = int(lesson_idx)
    s_data = await state.get_data()
    club_id = s_data.get("club_id")

    try:
        lessons_list = club_settings["disciplines"][disc_id]["schedule"][day]
        if 0 <= lesson_idx < len(lessons_list):
            lessons_list.pop(lesson_idx)

            # СНАЧАЛА перерисовываем интерфейс, передавая день в аргумент manual_day
            await admin_schedule_choose_day(callback, state, club_settings, manual_day=day)

            # И ТОЛЬКО ПОСЛЕ ЭТОГО сохраняем изменения в БД и Redis
            await save_club_settings(session, redis, bot.token, club_id, club_settings)
            logger.success(f"🗑 Изменения расписания успешно сохранены в БД (Клуб {club_id})")

        else:
            logger.warning("Занятие не найдено, возможно уже удалено.")
            await admin_schedule_choose_day(callback, state, club_settings, manual_day=day)

    except Exception as e:
        logger.error(f"Ошибка удаления расписания: {e}")
        # Аварийно вытаскиваем админа в меню дня, тоже используя аргумент manual_day
        await admin_schedule_choose_day(callback, state, club_settings, manual_day=day)


# ПЕРЕХОД К ВВОДУ ВРЕМЕНИ ДЛЯ НОВОГО ЗАНЯТИЯ
# =====================================================================
@router.callback_query(F.data == "adm_sch_start_input_time")
async def admin_schedule_trigger_time_input(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminScheduleStates.add_time)
    await callback.message.answer(
        "⏱ <b>Шаг 1 из 3: Введите время начала занятия</b>\n\n"
        "Отправьте текст в формате ЧЧ:ММ (например: <code>19:30</code>):",
        parse_mode="HTML"
    )
    await callback.answer()


# ==========================================
# ШАГ 3: Ловим ввод ВРЕМЕНИ и просим тренера
# ==========================================
@router.message(AdminScheduleStates.add_time)
async def admin_add_schedule_time(message: types.Message, state: FSMContext):
    time_text = message.text.strip()
    
    # Проверка формата ЧЧ:ММ
    if ":" not in time_text or len(time_text) != 5:
        return await message.answer("❌ Неверный формат! Введите время строго в формате ЧЧ:ММ (например, 18:00):")
        
    await state.update_data(new_time=time_text)
    await state.set_state(AdminScheduleStates.add_coach)
    
    await message.answer(
        "👤 <b>Шаг 2 из 3: Введите имя тренера или название группы</b>\n\n"
        "Например: <i>Омаров А.</i> или <i>Общая группа</i>:",
        parse_mode="HTML"
    )


# ==========================================
# ШАГ 4: Ловим ввод ТРЕНЕРА и просим места
# ==========================================
@router.message(AdminScheduleStates.add_coach)
async def admin_add_schedule_coach(message: types.Message, state: FSMContext):
    await state.update_data(new_coach=message.text.strip())
    await state.set_state(AdminScheduleStates.add_slots)
    
    await message.answer(
        "🔢 <b>Шаг 3 из 3: Укажите лимит свободных мест на занятие</b>\n\n"
        "Введите максимальное количество атлетов (только число, например: 15):",
        parse_mode="HTML"
    )


# ==========================================
# ШАГ 5: Финал! Ловим места и сохраняем в базу и Redis
# ==========================================
@router.message(AdminScheduleStates.add_slots)
async def admin_finalize_schedule(
    message: types.Message,
    state: FSMContext,
    club_settings: dict,
    session,
    redis: Redis,
    bot
):
    if not message.text.isdigit():
        return await message.answer("❌ Лимит мест должен быть целым числом! Попробуйте еще раз:")
        
    max_slots = int(message.text)
    s_data = await state.get_data()
    
    disc_id = s_data["disc_id"]
    club_id = s_data["club_id"]
    day = s_data["chosen_day"]
    time_text = s_data["new_time"]
    coach_text = s_data["new_coach"]
    
    new_lesson = {
        "time": time_text,
        "coach": coach_text,
        "max_slots": max_slots,
        "taken_slots": 0
    }
    
    # Защита: если там была старая строка, пересобираем в пустую структуру расписания
    discipline_block = club_settings["disciplines"][disc_id]
    if "schedule" not in discipline_block or isinstance(discipline_block["schedule"], str):
        club_settings["disciplines"][disc_id]["schedule"] = {
            "mon": [], "tue": [], "wed": [], "thu": [], "fri": [], "sat": [], "sun": []
        }
        
    # Добавляем новую тренировку в нужный день
    club_settings["disciplines"][disc_id]["schedule"][day].append(new_lesson)
    
    # Авто-сортировка по времени, чтобы в базе всё лежало по порядку (09:00, 12:00, 19:00...)
    club_settings["disciplines"][disc_id]["schedule"][day].sort(key=lambda x: x["time"])
    
    # Твоя родная функция сохранения в Postgres + автоматический пуш в Redis!
    await save_club_settings(session, redis, bot.token, club_id, club_settings)
    await state.clear()
    
    day_names = {"mon": "Понедельник", "tue": "Вторник", "wed": "Среда", "thu": "Четверг", "fri": "Пятница", "sat": "Суббота", "sun": "Воскресенье"}
    
    await message.answer(
        f"✅ <b>Занятие успешно добавлено в расписание!</b>\n\n"
        f"📅 День: <b>{day_names[day]}</b>\n"
        f"⏱ Время: <b>{time_text}</b>\n"
        f"👤 Инструктор: <b>{coach_text}</b>\n"
        f"🔢 Мест в группе: <b>{max_slots}</b>",
        parse_mode="HTML"
    )


#FSM FSM FSM Youkassa Youkassa
@router.callback_query(F.data == "admin_setup_yookassa")
async def start_yookassa_setup(callback: types.CallbackQuery, state: FSMContext):
    """Начало настройки: запрашиваем Shop ID"""
    await state.set_state(YooKassaSetupStates.waiting_for_shop_id)

    cancel_kb = InlineKeyboardBuilder()
    cancel_kb.row(types.InlineKeyboardButton(text="❌ Отмена", callback_data="admin_settings"))

    await callback.message.edit_text(
        "📥 <b>Настройка интеграции с ЮKassa</b>\n\n"
        "Введите ваш <b>Shop ID</b> (Идентификатор магазина).\n"
        "Вы можете найти его в личном кабинете ЮKassa вверху страницы (обычно состоит только из цифр).",
        reply_markup=cancel_kb.as_markup(),
        parse_mode="HTML"
    )


@router.message(YooKassaSetupStates.waiting_for_shop_id)
async def process_shop_id(message: types.Message, state: FSMContext):
    """Принимаем Shop ID и запрашиваем Secret Key"""
    shop_id = message.text.strip()

    if not shop_id.isdigit():
        return await message.answer("⚠️ Ошибка! Shop ID должен состоять только из цифр. Попробуйте еще раз:")

    await state.update_data(shop_id=shop_id)
    await state.set_state(YooKassaSetupStates.waiting_for_secret_key)

    await message.answer(
        "🔑 Теперь введите ваш <b>Секретный ключ</b> (Секрет).\n\n"
        "Его можно сгенерировать в ЛК ЮKassa в разделе <i>«Интеграция» -> «Ключи API»</i>.\n"
        "Он начинается на <code>test_...</code> (для тестового режима) или <code>live_...</code> (для боевого).",
        parse_mode="HTML"
    )


@router.message(YooKassaSetupStates.waiting_for_secret_key)
async def process_secret_key(
        message: types.Message,
        state: FSMContext,
        session: AsyncSession,
        club_id: int
):
    """Принимаем Secret Key и сохраняем всё в JSONB поле базы данных"""
    secret_key = message.text.strip()

    if not (secret_key.startswith("test_") or secret_key.startswith("live_")):
        return await message.answer(
            "⚠️ Ошибка! Неверный формат ключа. Он должен начинаться с <code>test_</code> или <code>live_</code>.\n"
            "Попробуйте ввести ключ заново:",
            parse_mode="HTML"
        )

    user_data = await state.get_data()
    shop_id = user_data["shop_id"]

    await state.clear()

    # 💾 Используем with_for_update() для безопасной мутации JSONB настроек
    result = await session.execute(
        select(Club)
        .where(Club.id == club_id)
        .with_for_update()
    )
    club = result.scalar_one_or_none()

    if club:
        # Для 100% надежности в асинхронной среде сделаем копию словаря
        current_settings = copy.deepcopy(club.club_settings) if club.club_settings else {}

        if "payments" not in current_settings:
            current_settings["payments"] = {}

        # Записываем данные в JSONB структуру
        current_settings["payments"]["provider"] = "yookassa"
        current_settings["payments"]["yookassa_shop_id"] = shop_id
        current_settings["payments"]["yookassa_secret_key"] = secret_key

        if "features" not in current_settings:
            current_settings["features"] = {}
        current_settings["features"]["online_payments"] = True

        # Присваиваем обновленный словарь обратно модели
        club.club_settings = current_settings
        
        # ⚡ Принудительно взводим флаг изменений для Алхимии, чтобы апдейт улетел в БД
        flag_modified(club, "club_settings")

        # Теперь Postgres на Аэзе железно применит UPDATE
        await session.commit()

        back_kb = InlineKeyboardBuilder()
        back_kb.row(types.InlineKeyboardButton(text="⚙️ Вернуться в настройки", callback_data="admin_settings"))

        await message.answer(
            "✅ <b>Интеграция успешно настроена!</b>\n\n"
            f"<b>Shop ID:</b> <code>{shop_id}</code>\n"
            f"<b>Ключ:</b> <code>{secret_key[:8]}...****</code>\n\n"
            "Теперь ваши клиенты смогут привязывать карты и оплачивать подписки онлайн.",
            reply_markup=back_kb.as_markup(),
            parse_mode="HTML"
        )
    else:
        await message.answer("🚨 Произошла критическая ошибка: Клуб не найден в базе данных.")


@router.callback_query(F.data == "manage_club_limits")
async def manage_club_limits_handler(callback: types.CallbackQuery, club: Club):
    """Экран управления лимитами клуба"""
    club_settings = club.club_settings or {}
    limits = club_settings.get("limits", {})

    # Достаем текущие значения из JSONB или берем наши дефолты
    timeout = limits.get("session_timeout_minutes", 150)
    freeze_step = limits.get("freeze_days_step", 7)

    text = f"⚙️ <b>Управление лимитами клуба «{club.name}»</b>\n\n" \
           f"⏱ <b>Сессия визита (СКУД):</b> <code>{timeout} мин.</code> ({timeout / 60:.1f} ч.)\n" \
           f"<i>В течение этого времени повторные проходы через турникет не списывают занятия.</i>\n\n" \
           f"❄️ <b>Шаг заморозки абонемента:</b> <code>{freeze_step} дн.</code>\n" \
           f"<i>Минимальный пакет дней, на который списывается заморозка.</i>\n\n" \
           f"Выберите параметр для изменения:"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱ Изменить время сессии", callback_data="change_limit_session")],
        [InlineKeyboardButton(text="❄️ Изменить шаг заморозки", callback_data="change_limit_freeze")],
        [InlineKeyboardButton(text="⬅️ Назад в настройки", callback_data="admin_settings")]
    ])

    await callback.message.edit_text(text=text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


# === ИЗМЕНЕНИЕ СЕССИИ ВИЗИТА ===
@router.callback_query(F.data == "change_limit_session")
async def change_limit_session(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminSettingsSG.waiting_for_session_timeout)
    await callback.message.answer("⏱ <b>Введите новое время сессии визита в минутах</b> (например, 120 для 2 часов):",
                                  parse_mode="HTML")
    await callback.answer()


# ==========================================
# ⏱ ИСПРАВЛЕННЫЙ ХЕНДЛЕР СЕССИИ ВИЗИТА (Строка 1808)
# ==========================================
@router.message(AdminSettingsSG.waiting_for_session_timeout)
async def process_session_timeout(
        message: types.Message,
        state: FSMContext,
        session: AsyncSession,
        club: Club,
        redis: Redis
):
    if not message.text.isdigit():
        return await message.answer("❌ Ошибка: Введите целое число минут!")

    minutes = int(message.text)
    if minutes < 1 or minutes > 1440:
        return await message.answer("❌ Ошибка: Время сессии должно быть в диапазоне от 1 до 1440 минут (24 часа)!")

    if not club.club_settings or not isinstance(club.club_settings, dict):
        club.club_settings = {}
    if "limits" not in club.club_settings or not isinstance(club.club_settings["limits"], dict):
        club.club_settings["limits"] = {}

    club.club_settings["limits"]["session_timeout_minutes"] = minutes

    try:
        db_club = await session.merge(club)
        flag_modified(db_club, "club_settings")
        await session.commit()

        # Полный сброс кэша в Redis для этого токена бота
        await redis.delete(f"club_config:{message.bot.token}")
    except Exception as e:
        logger.error(f"Ошибка при сохранении таймаута СКУД: {e}")
        await session.rollback()
        return await message.answer("❌ Не удалось сохранить изменения лимитов в БД.")

    await state.clear()
    await message.answer(f"✅ <b>Время СКУД-сессии успешно изменено на {minutes} минут!</b>", parse_mode="HTML")


# === ИЗМЕНЕНИЕ ШАГА ЗАМОРОЗКИ ===
@router.callback_query(F.data == "change_limit_freeze")
async def change_limit_freeze(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminSettingsSG.waiting_for_freeze_step)
    await callback.message.answer("❄️ <b>Введите новый минимальный шаг заморозки в днях</b> (например, 7):",
                                  parse_mode="HTML")
    await callback.answer()


# ❄️ ИСПРАВЛЕННЫЙ ХЕНДЛЕР ШАГА ЗАМОРОЗКИ (Строка 1844)
# ==========================================
@router.message(AdminSettingsSG.waiting_for_freeze_step)
async def process_freeze_step(
        message: types.Message,
        state: FSMContext,
        session: AsyncSession,
        club: Club,
        redis: Redis
):
    if not message.text.isdigit():
        return await message.answer("❌ Ошибка: Введите целое число дней!")

    days = int(message.text)
    if days < 1 or days > 30:
        return await message.answer("❌ Ошибка: Шаг заморозки должен быть от 1 до 30 дней!")

    if not club.club_settings or not isinstance(club.club_settings, dict):
        club.club_settings = {}
    if "limits" not in club.club_settings or not isinstance(club.club_settings["limits"], dict):
        club.club_settings["limits"] = {}

    club.club_settings["limits"]["freeze_days_step"] = days

    try:
        db_club = await session.merge(club)
        flag_modified(db_club, "club_settings")
        await session.commit()

        # Полный сброс кэша в Redis для этого токена бота
        await redis.delete(f"club_config:{message.bot.token}")
    except Exception as e:
        logger.error(f"Ошибка при сохранении шага заморозки: {e}")
        await session.rollback()
        return await message.answer("❌ Не удалось сохранить изменения шага заморозки в БД.")

    await state.clear()
    await message.answer(f"✅ <b>Минимальный шаг заморозки успешно изменен на {days} дней!</b>", parse_mode="HTML")
