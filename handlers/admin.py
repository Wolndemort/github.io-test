import asyncio
from aiogram import Router, types, F
from database.db import get_all_users_count, get_active_subs_count
from config import ADMIN_ID
from handlers.buttons import get_bjj_keyboard, get_kids_keyboard, get_main_menu_keyboard, admin_keyboard
from database.db import has_subscription, add_abon
from aiogram.filters import Command
import sqlite3
from config import db_file
import os
import pandas as pd
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import FSInputFile
from datetime import datetime, timedelta


router = Router()


class AdminStates(StatesGroup):
    waiting_for_broadcast_text = State()


@router.message(Command('admin'), F.from_user.id == ADMIN_ID)
async def admin_panel(message: types.Message):
    all_users = get_all_users_count()
    active_subs = get_active_subs_count()
    text = (
        "📈 <b>Панель администратора AE Maykop</b>\n\n"
        f"👥 Всего пользователей в базе: <code>{all_users}</code>\n"
        f"💳 Активных абонементов: <code>{active_subs}</code>\n\n"
        "Чего желаете, босс?"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=admin_keyboard())


@router.callback_query(F.data == 'begin')
async def process_begin(callback: types.CallbackQuery):
    await callback.message.edit_text(
        text='Вы вернулись в главное меню.Какой у вас вопрос?',
        reply_markup=get_main_menu_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == 'profile')
async def show_profile(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    is_active, expire_date = has_subscription(user_id)

    if is_active is True:
        date_str = expire_date.strftime('%d.%m.%Y %H:%M')
        await callback.message.answer(
            f"✅ <b>Ваш абонемент активен!</b>\n"
            f"📅 Истекает: <code>{date_str}</code>\n"
            f"Вам придет уведомление об окончании! 🥊",
            parse_mode="HTML"
        )
    elif is_active is False:
        await callback.message.answer("❌ Ваш абонемент истек. Продлите его в разделе «Абонементы»")

    else:
        await callback.message.answer("💎 У вас нет активного абонемента. Купите доступ, нажав кнопку ниже.")

    await callback.message.answer('Главное меню', reply_markup=get_main_menu_keyboard())


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
    text = (
        "💰 <b>Стоимость абонементов BJJ:</b>\n\n"
        "• Первая тренировка бесплатно— 700₽\n"
        "• Разовая тренировка — 700₽\n"
        "• Месяц (24 занятия) — 5000₽\n"
        "• Безлимит на год — 55 000₽"
    )
    await callback.message.edit_text(text=text, parse_mode='HTML', reply_markup=get_bjj_keyboard())
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
    text = (
        "💰 <b>Стоимость детского абонемента:</b>\n\n"
        "• Пробная тренировка — БЕСПЛАТНО\n"
        "• Месяц занятий — 5000₽\n"
    )
    await callback.message.edit_text(text=text, parse_mode='HTML', reply_markup=get_kids_keyboard())
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
    # Разрезаем 'buy_mma' -> получаем 'mma'
    sport_type = callback.data.split('_')[1]
    prices_map = {
        "mma": 1,
        "bjj": 1,
        "kids": 1
    }

    amount = prices_map.get(sport_type, 1)
    await callback.message.answer_invoice(
        title=f"Абонемент: {sport_type.upper()} 🥊",
        description=f"Доступ к тренировкам {sport_type.upper()} на 30 дней",
        payload=f"pay_{sport_type}",  # Этот текст придет к нам после оплаты
        provider_token="",
        currency="XTR",
        prices=[types.LabeledPrice(label="Абонемент 30 дней", amount=amount)],
        start_parameter="gym_sub"
    )


@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: types.PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def got_payments(message: types.Message):
    user_id = message.from_user.id
    payment_info = message.successful_payment
    payload = payment_info.invoice_payload
    sport_name = payload.split('_')[1].upper()

    new_date = add_abon(user_id)

    admin_id = 1271717628
    admin_text = (
        f"🔔 <b>Новая оплата!</b>\n\n"
        f"👤 Клиент: {message.from_user.full_name}\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"🥋 Секция: <b>{sport_name}</b>\n"
        f"💰 Сумма: {payment_info.total_amount} Stars (XTR)"
    )
    try:
        # Отправляем админу (используем message.bot, чтобы не импортировать бота)
        await message.bot.send_message(admin_id, admin_text, parse_mode="HTML")
    except Exception as e:
        print(f"Ошибка уведомления админа: {e}")

    await message.answer(
        f"🎉 <b>Оплата прошла успешно!</b>\n"
        f"Ваш абонемент на <b>{sport_name}</b> продлен до: <code>{new_date}</code>",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard()  # Даем меню, чтобы он мог проверить статус
    )


@router.callback_query(F.data == 'admin_broadcast', F.from_user.id == ADMIN_ID)
async def start_broadcast(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "📝 <b>Отправьте сообщение для рассылки:</b>\n\nЯ перешлю его всем пользователям (можно с фото/видео).",
        parse_mode="HTML")
    await state.set_state(AdminStates.waiting_for_broadcast_text)
    await callback.answer()


@router.message(AdminStates.waiting_for_broadcast_text, F.from_user.id == ADMIN_ID)
async def perform_broadcast(message: types.Message, state: FSMContext):
    with sqlite3.connect(db_file) as conn:
        cur = conn.cursor()
        users = cur.execute('SELECT user_id FROM users').fetchall()

    count = 0
    await message.answer(f"🚀 Рассылка началась (всего: {len(users)} чел.)...")

    for user_data in users:
        try:
            await message.copy_to(chat_id=user_data[0])
            count += 1
            await asyncio.sleep(0.05)
        except Exception:
            continue

    await message.answer(f"✅ <b>Рассылка завершена!</b>\nДоставлено: <code>{count}</code> пользователям.",
                         parse_mode="HTML")
    await state.clear()


@router.callback_query(F.data == 'export_db', F.from_user.id == ADMIN_ID)
async def export_database(callback: types.CallbackQuery):
    await callback.answer("⏳ Генерирую таблицу для пандас...")

    file_path = "users_data_raw.csv"

    try:
        with sqlite3.connect(db_file) as conn:
            df = pd.read_sql_query("SELECT * FROM users", conn)
            df.to_csv(file_path, index=False, encoding='utf-8')

        csv_file = FSInputFile(file_path)
        await callback.message.answer_document(
            csv_file,
            caption="📊 <b>Raw Data Export</b>\nФайл готов для обработки в Pandas."
        )
        os.remove(file_path)
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка выгрузки CSV: {e}")
        if os.path.exists(file_path):
            os.remove(file_path)


@router.callback_query(F.data == 'freeze_sub')
async def freeze_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id

    with sqlite3.connect(db_file) as conn:
        cur = conn.cursor()
        cur.execute("SELECT expire_date , can_freeze FROM users WHERE user_id = ?", (user_id,))
        res = cur.fetchone()

        if not res or res[1] == 0:
            return await callback.answer("🚫 Заморозка недоступна или уже использована!", show_alert=True)

        expire_date_str = res[0]

        current_expire = datetime.strptime(expire_date_str, '%Y-%m-%d %H:%M:%S')
        new_expire = current_expire + timedelta(days=5)
        new_expire_str = new_expire.strftime('%Y-%m-%d %H:%M:%S')

        cur.execute("UPDATE users SET expire_date = ?, can_freeze = 0 WHERE user_id = ?",
                    (new_expire_str, user_id))
        conn.commit()

    await callback.message.answer(
        f"❄️ <b>Абонемент заморожен на 5 дней!</b>\n\n"
        f"Новая дата окончания: <code>{new_expire_str}</code>\n"
        f"Функция станет доступна снова после следующей оплаты.",
        parse_mode='HTML'
    )
    await callback.answer()



