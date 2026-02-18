import asyncio
from database.db import get_all_users_count, get_active_subs_count, Session, User, engine, get_daily_stats
from config import ADMIN_IDS, secret_key
from sqlalchemy import select
from handlers.buttons import get_bjj_keyboard, get_kids_keyboard, get_main_menu_keyboard, admin_keyboard, \
    get_scanner_keyboard, get_profile_keyboard
from database.db import has_subscription, add_abon
from aiogram.filters import Command
import os
import pandas as pd
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import FSInputFile
from datetime import timedelta
import qrcode
from io import BytesIO
from aiogram.types import BufferedInputFile
from aiogram import Router, F, types
from datetime import datetime
from loguru import logger
import hashlib
import hmac


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
async def admin_panel(message: types.Message):
    admin = message.from_user
    logger.info(f"🔑 Админ {admin.full_name} (ID: {admin.id}) открыл панель управления")
    try:
        all_users = get_all_users_count()
        active_subs = get_active_subs_count()
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
async def process_begin(callback: types.CallbackQuery):
    await callback.message.edit_text(
        text='Вы вернулись в главное меню.Какой у вас вопрос?',
        reply_markup=get_main_menu_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == 'check_status_now')
async def show_profile(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user_name = callback.from_user.full_name
    logger.debug(f"🔍 Юзер {user_name} (ID: {user_id}) нажал 'Проверить статус'")
    try:
        is_active, expire_date = has_subscription(user_id)
        logger.debug(f"📊 Результат для {user_id}: active={is_active}, date={expire_date}")
        text = "🔍 <b>Информация не найдена.</b>\nПожалуйста, обратитесь к администратору или купите абонемент."
        if is_active is True:
            date_str = expire_date.strftime('%d.%m.%Y %H:%M')
            text = (f"✅ <b>Ваш абонемент активен!</b>\n"
                    f"📅 Истекает: <code>{date_str}</code>\n"
                    f"Вам придет уведомление за 3 дня! 🥊")
        elif is_active is False:
            date_str = expire_date.strftime('%d.%m.%Y %H:%M') if expire_date else "неизвестно"
            text = (f"❌ <b>Ваш абонемент истек!</b>\n"
                    f"📅 Срок закончился: <code>{date_str}</code>\n"
                    f"Продлите его в разделе «Абонементы».")
        elif is_active is None:
            text = "💎 <b>У вас нет активного абонемента.</b>\nВы можете приобрести его в главном меню."
        await callback.message.edit_text(
            text=text,
            reply_markup=get_profile_keyboard(),
            parse_mode="HTML"
            )
    except Exception as e:
        if "message is not modified" in str(e):
            logger.debug(f"ℹ️ Сообщение для {user_id} не изменилось (данные те же)")
            await callback.answer("Данные актуальны")
        else:
            logger.error(f"❌ Ошибка в show_profile для {user_id}: {e}")
            await callback.answer()


@router.callback_query(F.data == 'profile')
async def open_profile_menu(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "👤 <b>Личный кабинет</b>\n\nЗдесь вы можете проверить свой абонемент,"
        " получить QR-код для входа или оформить заморозку.",
        reply_markup=get_profile_keyboard(),
        parse_mode="HTML"
    )


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
        text='🤼‍♂️ <b>GRAPPLING KIDS (Детская борьба)</b>\n'
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
        "💰 <b>Стоимость абонементов BJJ:</b>\n\n"
        "• Первая тренировка бесплатно— 700₽\n"
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
        "🗓 <b>Расписание BJJ:</b>\n\n"
        "• Вторник: 20:00\n"
        "• Вторник: 20:00\n"
        "• Четверг: 20:00\n"
        "• Суббота: 12:00"
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
        "• Месяц занятий — 5000₽\n"
    )
    try:
        await callback.message.edit_text(
            text=text,
            parse_mode='HTML',
            reply_markup=get_kids_keyboard()
        )
    except Exception as e:
        if "message is not modified" not in str(e):
            logger.error(f"❌ Ошибка при показе прайса BJJ для {user.id}: {e}")

    await callback.answer()


@router.callback_query(F.data == 'schedule_kids')
async def kids_schedule(callback: types.CallbackQuery):
    text = (
        "🗓 <b>Расписание GRAPPLING KIDS:</b>\n\n"
        "• Вторник: 17:00\n"
        "• Четверг: 17:00\n"
    )
    await callback.message.edit_text(text=text, parse_mode='HTML', reply_markup=get_kids_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith('buy_'))
async def buy_handler(callback: types.CallbackQuery):
    user = callback.from_user
    # Разрезаем 'buy_mma' -> получаем 'mma'
    sport_type = callback.data.split('_')[1]
    logger.info(f"💳 Попытка покупки: {user.full_name} (ID: {user.id}) -> Направление: {sport_type.upper()}")
    prices_map = {
        "mma": 1,
        "bjj": 1,
        "kids": 1
    }

    amount = prices_map.get(sport_type, 1)
    try:
        await callback.message.answer_invoice(
            title=f"Абонемент: {sport_type.upper()} 🥊",
            description=f"Доступ к тренировкам {sport_type.upper()} на 30 дней",
            payload=f"pay_{sport_type}",  # Этот текст придет к нам после оплаты
            provider_token="",
            currency="XTR",
            prices=[types.LabeledPrice(label="Абонемент 30 дней", amount=amount)],
            start_parameter="gym_sub")

        logger.debug(f"📜 Инвойс на {amount} XTR отправлен пользователю {user.id}")
    except Exception as e:
        # ЛОГ: Ошибка (например, неверный токен или валюта)
        logger.error(f"❌ Ошибка формирования инвойса для {user.id}: {e}")
        await callback.answer("Произошла ошибка при создании счета. Попробуйте позже.")
    await callback.answer()


@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: types.PreCheckoutQuery):
    logger.info(f"💳 PreCheckout от {pre_checkout_query.from_user.id} на сумму {pre_checkout_query.total_amount}")
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def got_payments(message: types.Message):
    user_id = message.from_user.id
    user_name = message.from_user.full_name
    payment_info = message.successful_payment
    payload = payment_info.invoice_payload
    sport_name = payload.split('_')[1].upper()
    logger.success(
        f"💰 ОПЛАТА ПОЛУЧЕНА: {user_name} (ID: {user_id}) | Сумма: {payment_info.total_amount} Stars | Секция: "
        f"{sport_name}")
    try:
        new_date_str = add_abon(user_id, full_name=user_name)
        if not new_date_str:
            logger.critical(f"🆘 ОШИБКА БД ПОСЛЕ ОПЛАТЫ: Юзер {user_id} заплатил, но add_abon вернул None!")
            await message.answer(
                "⚠️ Оплата прошла успешно, но возник сбой при активации. Мы уже знаем об этом и скоро всё исправим!")
        else:
            logger.info(f"✅ Абонемент для {user_id} подтвержден до {new_date_str}")
        admin_id = 1271717628
        admin_text = (
            f"🔔 <b>Новая оплата!</b>\n\n"
            f"👤 Клиент: {message.from_user.full_name}\n"
            f"🆔 ID: <code>{user_id}</code>\n"
            f"🥋 Секция: <b>{sport_name}</b>\n"
            f"💰 Сумма: {payment_info.total_amount} Stars (XTR)"
        )

        try:
            await message.bot.send_message(admin_id, admin_text, parse_mode="HTML")
            logger.debug(f"📲 Уведомление об оплате отправлено админу {admin_id}")
        except Exception as e:
            logger.error(f"❌ Не удалось уведомить админа {admin_id}: {e}")

        await message.answer(
            f"🎉 <b>Оплата прошла успешно!</b>\n"
            f"Ваш абонемент на <b>{sport_name}</b> продлен до: <code>{new_date_str}</code>",
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard()
        )
    except Exception as e:
        logger.error(f"💥 Глобальная ошибка в successful_payment для {user_id}: {e}")


@router.callback_query(F.data == 'admin_broadcast', F.from_user.id.in_(ADMIN_IDS))
async def start_broadcast(callback: types.CallbackQuery, state: FSMContext):
    admin = callback.from_user
    logger.warning(f"📢 Админ {admin.full_name} (ID: {admin.id}) открыл меню рассылки")
    await callback.message.answer(
        "📝 <b>Отправьте сообщение для рассылки:</b>\n\nЯ перешлю его всем пользователям (можно с фото/видео).",
        parse_mode="HTML")
    await state.set_state(AdminStates.waiting_for_broadcast_text)
    await callback.answer()


@router.message(AdminStates.waiting_for_broadcast_text, F.from_user.id.in_(ADMIN_IDS))
async def perform_broadcast(message: types.Message, state: FSMContext):
    admin = message.from_user
    logger.warning(f"📢 Админ {admin.full_name} (ID: {admin.id}) запустил массовую рассылку!")
    try:
        with Session() as session:
            users_ids = session.scalars(select(User.user_id)).all()
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
            await message.send_copy(chat_id=user_id)
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
    admin = callback.from_user
    logger.warning(f"📥 Админ {admin.full_name} (ID: {admin.id}) запросил экспорт всей базы в CSV!")
    await callback.answer("⏳ Генерирую таблицу для pandas...")
    file_path = "users_data_raw.csv"
    try:
        df = pd.read_sql_table('users', engine)
        df.to_csv(file_path, index=False, encoding='utf-8-sig')
        logger.debug(f"Файл {file_path} успешно сформирован (строк: {len(df)})")
        csv_file = FSInputFile(file_path)
        await callback.message.answer_document(csv_file,
                                               caption="📊 <b>Raw Data Export</b>\nФайл готов для обработки в Pandas.")
        logger.success(f"✅ База данных отправлена админу {admin.id}")
        if os.path.exists(file_path):
            os.remove(file_path)

    except Exception as e:
        logger.error(f"❌ Ошибка выгрузки CSV для админа {admin.id}: {e}")
        await callback.message.answer(f"❌ Ошибка выгрузки CSV: {e}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.debug(f"Временный файл {file_path} удален")


@router.callback_query(F.data == 'daily_report', F.from_user.id.in_(ADMIN_IDS))
async def show_daily_report(callback: types.CallbackQuery):
    admin = callback.from_user
    logger.info(f"📊 Админ {admin.full_name} (ID: {admin.id}) запросил дневной отчет")
    try:
        visits, active = get_daily_stats()
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


@router.callback_query(F.data == "freeze_sub")
async def freeze_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user_name = callback.from_user.full_name
    logger.info(f"❄️ Запрос заморозки: {user_name} (ID: {user_id})")
    try:
        with Session()as session:
            user = session.get(User, user_id)
            now = datetime.now()
            if not user or not user.expire_date:
                logger.debug(f"🚫 Отказ заморозки {user_id}: нет абонемента")
                return await callback.answer("🚫 У вас нет активного абонемента!", show_alert=True)
            if user.can_freeze == 0:
                logger.debug(f"🚫 Отказ заморозки {user_id}: лимит исчерпан")
                return await callback.answer("🚫 Заморозка уже была использована!", show_alert=True)
            if user.expire_date < now:
                logger.debug(f"🚫 Отказ заморозки {user_id}: абонемент просрочен")
                return await callback.answer("❌ Нельзя заморозить просроченный абонемент!", show_alert=True)

            old_date = user.expire_date
            user.expire_date += timedelta(days=5)
            user.can_freeze = 0

            new_date_str = user.expire_date.strftime('%d.%m.%Y %H:%M')
            session.commit()
            logger.success(f"✅ УСПЕХ: {user_id} заморожен. Было: {old_date.strftime('%d.%m')} -> Стало: {new_date_str}")

        await callback.message.edit_text(
            f"❄️ <b>Абонемент заморожен на 5 дней!</b>\n\n"
            f"📅 Новая дата окончания: <code>{new_date_str}</code>\n\n"
            f"<i>Заморозка станет доступна снова при покупке нового абонемента.</i>",
            reply_markup=get_profile_keyboard(),
            parse_mode='HTML'
            )
    except Exception as e:
        if "message is not modified" in str(e):
            await callback.answer("Данные обновлены!", show_alert=True)
        else:
            logger.error(f"❌ Ошибка при заморозке для {user_id}: {e}")
            await callback.answer("Произошла ошибка при связи с базой данных", show_alert=True)

        await callback.answer()


@router.callback_query(F.data == 'show_qr')
async def send_user_qr(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user_name = callback.from_user.full_name
    logger.debug(f"🎟 Запрос QR-пропуска: {user_name} (ID: {user_id})")
    try:
        time_salt = datetime.now().strftime('%Y-%m-%d-%H')
        signature = generate_signature(user_id, time_salt)
        qr_data = f'user:{user_id}:{time_salt}:{signature}'
        qr_img = qrcode.make(qr_data)
        buffer = BytesIO()
        qr_img.save(buffer, format='PNG')
        buffer.seek(0)
        photo = BufferedInputFile(buffer.getvalue(), filename=f"qr_{user_id}.png")
        await callback.message.answer_photo(
            photo=photo,
            caption="🎟 **Ваш динамический пропуск**\n\nПокажите этот код камере на входе. "
                    "Система автоматически проверит абонемент и спишет занятие."
        )
        logger.success(f"✅ QR-код успешно отправлен: {user_id} (salt: {time_salt})")
        await callback.answer()
    except Exception as e:
        logger.error(f"❌ Ошибка генерации QR для {user_id}: {e}")
        await callback.answer("Ошибка при создании QR-кода. Попробуйте позже.", show_alert=True)


@router.message(F.web_app_data)
async def parse_qr_scan(message: types.Message):
    raw_data = message.web_app_data.data
    logger.info(f"🔍 Сканер (Admin ID: {message.from_user.id}) считал данные: {raw_data}")
    try:
        parts = raw_data.split(':')
        if len(parts) != 4 or parts[0] != 'user':
            return await message.answer("❌ Ошибка: Неверный формат QR-кода")
        _, scanned_id_str, time_salt, signature = parts
        scanned_id = int(scanned_id_str)
        expected_sig = generate_signature(scanned_id, time_salt)
        if not hmac.compare_digest(signature, expected_sig):
            logger.warning(f"🚨 Попытка взлома! Поддельный QR: {raw_data}")
            return await message.answer("🚨 ВНИМАНИЕ: QR-код подделан или изменен!")

    except Exception as e:
        logger.error(f"❌ Ошибка валидации данных: {e}")
        return await message.answer("❌ Ошибка при чтении данных кода")
    now = datetime.now()
    current_salt = now.strftime('%Y-%m-%d-%H')
    previous_hour_salt = (now - timedelta(hours=1)).strftime('%Y-%m-%d-%H')
    if time_salt != current_salt and time_salt != previous_hour_salt:
        logger.info(f"⌛ Истекший QR: {scanned_id} (код за {time_salt})")
        return await message.answer(
            "⌛ Срок действия QR истек.\n"
            "Пожалуйста, обновите пропуск в боте."
        )
    if time_salt == previous_hour_salt and now.minute > 5:
        logger.warning(f"🚫 Слишком старый QR (прошлый час): {scanned_id}")
        return await message.answer("⌛ Этот код слишком старый. Обновите его.")
    with Session() as session:
        user = session.get(User, scanned_id)
        if not user:
            logger.warning(f"🚫 Вход отклонен: Пользователь ID {scanned_id} не найден")
            return await message.answer("❌ Пользователь не найден в базе!")
        if user.last_visit:
            logger.debug(f"⏳ Повторный вход (анти-флуд): {user.full_name} (ID: {scanned_id})")
            if (now - user.last_visit).total_seconds() < 300:
                return await message.answer(
                    f"⚠️ {user.full_name} уже отмечен!\nПовторный вход через 5 минут."
                )
        if not user.expire_date:
            logger.warning(f"🚫 Вход отклонен: У {user.full_name} ({scanned_id}) нет абонемента")
            return await message.answer(f"❓ У {user.full_name} нет активного абонемента.")

        if user.expire_date < now:
            logger.warning(f"🔴 ДОСТУП ЗАПРЕЩЕН: {user.full_name} (истек {user.expire_date.strftime('%d.%m')})")
            return await message.answer(
                f"🔴 ДОСТУП ЗАПРЕЩЕН\n👤 {user.full_name}\n"
                f"❌ Срок истек: {user.expire_date.strftime('%d.%m.%Y')}"
            )
        if user.is_frozen == 1:
            user.is_frozen = 0
            logger.info(f"🧊 Авто-разморозка при входе: {user.full_name}")
            await message.answer(f"❄️ Абонемент {user.full_name} автоматически разморожен.")
        user.balance_lessons += 1
        user.last_visit = now
        current_lessons = user.balance_lessons
        logger.success(f"🔓 ДОСТУП РАЗРЕШЕН: {user.full_name} (ID: {scanned_id}). Посещение №{current_lessons}")
        response_text = (
            f"🟢 <b>ПРОХОДИТЕ</b>\n"
            f"👤 {user.full_name}\n"
            f"✅ Действует до : {user.expire_date.strftime('%d.%m.%Y')}\n"
            f"📈 Посещений за период: {current_lessons}"
        )
        session.commit()
    await message.answer(response_text, parse_mode="HTML")
    try:
        await message.bot.send_message(
            scanned_id,
            f"🔔 Вход зафиксирован. Приятной тренировки!\n📈 Ваше посещение №{current_lessons}"
        )
    except Exception as e:
        logger.error(f"✉️ Не удалось отправить уведомление пользователю {scanned_id}: {e}")
