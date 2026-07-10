from datetime import datetime, timedelta
from sqlalchemy import update
from aiogram.utils.keyboard import InlineKeyboardBuilder
from redis import Redis
from services.yookassa_client import YooKassaClient
import uuid
from config import PROXY_URL
from database.db import PaymentOrder, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger
from database.db import Student, Club,Subscription 
from handlers.buttons import discipline, get_pay_options_kb, get_cash_options_kb
from database.db import add_abon
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, LabeledPrice, Message, PreCheckoutQuery
from aiogram import Router, F, types
from handlers.states import PaymentStates


router = Router()


@router.callback_query(F.data == 'choose_section')
async def show_sections(
    callback: types.CallbackQuery,
    club_settings: dict  # Прилетело из мидлвари
):
    # 1. Достаем дисциплины (они в корне конфига, а не в UI)
    disciplines = club_settings.get("disciplines", {})

    # 2. Проверяем, есть ли вообще хоть одна активная дисциплина
    # (Чтобы не показывать пустое меню)
    has_active = any(info.get("active") for info in disciplines.values())

    if not disciplines or not has_active:
        return await callback.answer(
            "В этом клубе направления еще не настроены 🛠",
            show_alert=True
        )

    # 3. Генерируем клавиатуру
    # Передаем весь club_settings, так как твоя функция `discipline()`
    # сама умеет вынимать оттуда нужные данные (как мы писали выше)
    await callback.message.edit_text(
        "<b>Выберите направление тренировок:</b>",
        reply_markup=discipline(club_settings),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith('buy_'))
async def select_athlete_handler(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    discipline_code = callback.data.split('_')[1] # boxing, kickboxing и т.д.
    await state.update_data(sport_type=discipline_code)

    # Ищем детей этого родителя в БД (с фильтром по parent_id)
    from database.db import Student
    res = await session.execute(select(Student).where(Student.parent_id == callback.from_user.id))
    students = res.scalars().all()

    # ПОДСТРАХОВКА: Если детей в базе еще нет
    if not students:
        return await callback.answer(
            "🙋‍♂️ У вас еще не зарегистрировано ни одного атлета!\n"
            "Пожалуйста, сначала добавьте ребенка в личном кабинете.",
            show_alert=True
        )

    # Генерируем клавиатуру ТОЛЬКО из реальных детей
    kb = InlineKeyboardBuilder()
    for s in students:
        kb.row(types.InlineKeyboardButton(text=f"👦 {s.name}", callback_data=f"set_at_{s.id}"))

    # Добавим кнопку возврата назад к выбору секций для удобства
    kb.row(types.InlineKeyboardButton(
        text="⬅️ Назад к секции", 
        callback_data=f"section_{discipline_code}"
    ))

    await callback.message.edit_text(
        "<b>Для кого оформляем абонемент?</b>\n\n"
        "Выберите ребенка из списка ниже:",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith('set_at_'))
async def athlete_chosen_handler(callback: types.CallbackQuery, state: FSMContext, club_settings: dict):
    target = callback.data.split('_')[2]
    student_id = callback.from_user.id if target == "me" else int(target)

    # ВОТ ТУТ МЫ ПОБЕЖДАЕМ NONE
    await state.update_data(student_id=student_id)

    data = await state.get_data()
    discipline_cfg = club_settings.get("disciplines", {}).get(data['sport_type'])

    # Показываем твою клавиатуру (8 зан, 12 зан и т.д.)
    markup = get_pay_options_kb(discipline_cfg, data['sport_type'])
    await callback.message.edit_text(f"🥋 Секция: <b>{discipline_cfg['name']}</b>\nВыберите тариф:", reply_markup=markup,
                                     parse_mode="HTML")


# Полностью заменяем хендлер set_limit_ на этот:
# Заменяем старый хендлер set_tariff_ на этот:
@router.callback_query(F.data.startswith('set_tariff_'))
async def process_kids_limit(
        callback: types.CallbackQuery,
        state: FSMContext,
        session: AsyncSession,  # Добавили сессию для проверки карты
        club_settings: dict
):
    parts = callback.data.split('_')
    try:
        sport_type = parts[2]
        tariff_idx = int(parts[3])
    except (IndexError, ValueError):
        return await callback.answer("Ошибка обработки кнопки тарифа ❌", show_alert=True)

    await state.update_data(sport_type=sport_type)

    discipline_cfg = club_settings.get("disciplines", {}).get(sport_type, {})
    if not discipline_cfg:
        return await callback.answer("Ошибка: секция не найдена 🛠", show_alert=True)

    tariffs = discipline_cfg.get("tariffs", [])
    if tariff_idx >= len(tariffs):
        return await callback.answer("Ошибка: выбранный тариф больше не существует ❌", show_alert=True)

    selected_tariff = tariffs[tariff_idx]
    price = selected_tariff.get('price')
    days = selected_tariff.get('days', 30)
    count = selected_tariff.get('count', 0)
    display_name = discipline_cfg.get('name', 'Секция')

    label = f"Безлимит на {days} дней" if count == 999 else f"{count} зан. / {days} дн."
    data = await state.get_data()
    await state.update_data(
        student_id=data.get('student_id') or callback.from_user.id,
        lesson_count=count,
        days_to_add=days,
        price=price,
        discipline_name=display_name,
        tariff_label=label
    )

    # 🔍 ПРОВЕРКА: Есть ли у родителя сохраненная карта в нашей базе?
    from database.db import Subscription
    sub_res = await session.execute(
        select(Subscription)
        .where(Subscription.user_id == callback.from_user.id)
        .where(Subscription.rebill_id.is_not(None))
        .limit(1)
    )
    saved_card = sub_res.scalar_one_or_none()

    # Формируем динамические кнопки
    inline_keyboard = []

    if saved_card:
        # Если карта есть — предлагаем оплату в 1 клик
        inline_keyboard.append([
            types.InlineKeyboardButton(text="⚡️ Оплатить сохраненной картой", callback_data="pay_one_click")
        ])
        inline_keyboard.append([
            types.InlineKeyboardButton(text="💳 Оплатить новой картой", callback_data="pay_method_official")
        ])
    else:
        # Если карты нет — стандартная первая оплата
        inline_keyboard.append([
            types.InlineKeyboardButton(text="💳 Онлайн оплата картой", callback_data="pay_method_official")
        ])

    # Всегда оставляем твою серую схему по СБП внизу
    inline_keyboard.append([
        types.InlineKeyboardButton(text="↩️ Перевод по СБП (Вручную по чеку)", callback_data="pay_method_sbp")
    ])

    kb = types.InlineKeyboardMarkup(inline_keyboard=inline_keyboard)

    text = (
        f"🎯 <b>Выбран тариф: {display_name}</b>\n"
        f"Условия: <b>{label} — {price}₽</b>\n\n"
        f"Пожалуйста, выберите способ оплаты:"
    )
    await callback.message.edit_text(text=text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == 'pay_one_click')
async def process_one_click_payment(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    await callback.answer("Провожу платеж в 1 клик...", show_alert=False)

    data = await state.get_data()
    user_id = callback.from_user.id

    # 1. Достаем токен сохраненной карты (rebill_id в ЮKassa — это payment_method_id)
    from database.db import Subscription, PaymentOrder, User, Club
    sub_res = await session.execute(select(Subscription).where(Subscription.user_id == user_id).limit(1))
    saved_card = sub_res.scalar_one_or_none()

    if not saved_card or not saved_card.rebill_id:
        return await callback.message.answer("❌ Ошибка: Сохраненная карта не найдена. Оплатите заново для привязки.")

    order_id = f"ONE_{uuid.uuid4().hex[:12].upper()}"
    amount_kopecks = int(float(data['price']) * 100)

    user_res = await session.execute(select(User).where(User.user_id == user_id))
    user = user_res.scalar_one_or_none()

    if not user or not user.club_id:
        return await callback.message.answer("❌ Ошибка: Не удалось определить ваш клуб.")

    # 2. Нам нужны платежные ключи этого клуба для проведения автосписания
    club_res = await session.execute(select(Club).where(Club.id == user.club_id))
    club = club_res.scalar_one_or_none()

    if not club:
        return await callback.message.answer("❌ Ошибка: Клуб не найден в системе.")

    pay_settings = club.club_settings.get("payments", {})
    shop_id = pay_settings.get("yookassa_shop_id")
    secret_key = pay_settings.get("yookassa_secret_key")

    if not shop_id or not secret_key:
        return await callback.message.answer("❌ Клуб еще не настроил онлайн-платежи. Оплата в 1 клик невозможна.")

    # Фиксируем заказ со статусом NEW и типом RECURRENT
    new_order = PaymentOrder(
        id=order_id,
        user_id=user_id,
        student_id=data['student_id'],
        club_id=user.club_id,
        amount_kopecks=amount_kopecks,
        lesson_count=data['lesson_count'],
        days_to_add=data['days_to_add'],
        status="NEW",
        type="RECURRENT"
    )
    session.add(new_order)
    await session.commit()

    # 3. Вызываем фоновое списание по токену карты через ЮKassa
    from config import PROXY_URL
    yookassa_node = YooKassaClient(
        shop_id=shop_id,
        secret_key=secret_key,
        proxy_url=PROXY_URL
    )

    charge_res = await yookassa_node.charge_payment(
        order_id=order_id,
        amount_kopecks=amount_kopecks,
        payment_method_id=saved_card.rebill_id,
        club_name=club.name
    )

    # 4. Проверяем статус. Если 'succeeded' — моментально активируем абонемент
    if charge_res.get("Success") and charge_res.get("Status") == "succeeded":
        new_order.status = "CONFIRMED"

        # Твоя родная функция начисления абонемента
        from handlers.payments import add_abon

        abon_result = await add_abon(
            student_id=data['student_id'],
            lessons_count=data['lesson_count'],
            session=session,
            club_id=user.club_id,
            club_settings=club.club_settings if club else {},
            days_to_add=data['days_to_add']
        )
        await session.commit()

        if abon_result:
            new_expire, _ = abon_result
            desc = "БЕЗЛИМИТ" if data['lesson_count'] == 999 else f"{data['lesson_count']} зан."
            await callback.message.edit_text(
                f"⚡️ <b>Оплата успешно проведена в 1 клик!</b>\n\n"
                f"Списано: <b>{data['price']}₽</b> с вашей сохраненной карты.\n"
                f"Абонемент (<b>{desc}</b>) успешно активирован и действует до: <b>{new_expire}</b>. 🔥"
            )
        await state.clear()
    else:
        new_order.status = "REJECTED"
        await session.commit()
        error_msg = charge_res.get("Message", "Недостаточно средств или карта заблокирована")
        await callback.message.answer(
            f"❌ Ошибка списания с сохраненной карты: {error_msg}.\n"
            f"Попробуйте оплатить по СБП или выберите оплату новой картой заново.")


@router.callback_query(F.data == 'pay_method_sbp')
async def process_sbp_payment_choice(
        callback: types.CallbackQuery,
        state: FSMContext,
        club_settings: dict
):
    """Сценарий ручной оплаты по реквизитам СБП (твоя старая схема)"""
    # Включаем твой родной стейт ожидания чека
    await state.set_state(PaymentStates.waiting_for_receipt)

    # Достаем сохраненные на прошлом шаге данные из FSM
    data = await state.get_data()

    # UI часть (Вывод реквизитов СБП из конфига клуба)
    ui_cfg = club_settings.get("ui", {})
    payment_info = ui_cfg.get("payment_info")

    if not payment_info or "+79000000000" in payment_info:
        payment_info = "⚠️ Реквизиты временно не указаны. Пожалуйста, свяжитесь с администратором."

    text = (
        f"💰 <b>Оплата по СБП: {data['discipline_name']}</b>\n"
        f"Тариф: <b>{data['tariff_label']} — {data['price']}₽</b>\n\n"
        f"💳 <b>Реквизиты для перевода (СБП):</b>\n"
        f"<code>{payment_info}</code>\n\n"
        f"<b>Шаг 1:</b> Переведите {data['price']}₽ по указанным реквизитам.\n"
        f"<b>Шаг 2:</b> Пришлите <b>скриншот чека</b> сюда в ответ на это сообщение.\n\n"
        f"<i>После проверки админом абонемент активируется автоматически.</i>"
    )

    await callback.message.edit_text(
        text=text,
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == 'pay_method_official')
async def process_official_card_payment(
        callback: types.CallbackQuery,
        state: FSMContext,
        session: AsyncSession
):
    """Сценарий онлайн-оплаты через ЮKassa: Ссылка (первый раз) ИЛИ 1 клик (если карта привязана)"""
    await callback.answer("Обрабатываю запрос...", show_alert=False)

    data = await state.get_data()
    user_id = callback.from_user.id

    student_id = data['student_id']
    price = data['price']
    amount_kopecks = int(float(price) * 100)
    lesson_count = data['lesson_count']
    days_to_add = data['days_to_add']

    # 1. Проверяем настройки клуба
    user_res = await session.execute(select(User).where(User.user_id == user_id))
    user = user_res.scalar_one_or_none()

    if not user or not user.club_id:
        return await callback.message.answer("❌ Ошибка: Клуб не найден в вашей учетной записи.")

    club_res = await session.execute(select(Club).where(Club.id == user.club_id))
    club = club_res.scalar_one_or_none()

    if not club:
        return await callback.message.answer("❌ Ошибка: Клуб не найден в базе данных платформы.")

    pay_settings = club.club_settings.get("payments", {}) if club.club_settings else {}
    shop_id = pay_settings.get("yookassa_shop_id")
    secret_key = pay_settings.get("yookassa_secret_key")

    if not shop_id or not secret_key:
        return await callback.message.answer("⚠️ Онлайн-оплата картой временно недоступна для этого клуба.")

    # 2. 🔥 ПРОВЕРЯЕМ, ЕСТЬ ЛИ СОХРАНЕННАЯ КАРТА (Как у Velvet VPN)
    sub_query = select(Subscription).where(
        Subscription.user_id == user_id,
        Subscription.club_id == user.club_id,
        Subscription.rebill_id.isnot(None)
    )
    sub_res = await session.execute(sub_query)
    saved_subscription = sub_res.scalar_one_or_none()

    # Формируем уникальный ID заказа для нашей СУБД
    order_id = f"INIT_{uuid.uuid4().hex[:12].upper()}"

    # Инициализируем ноду ЮKassa
    yookassa_node = YooKassaClient(shop_id=shop_id, secret_key=secret_key, proxy_url=PROXY_URL)

    # =====================================================================
    # СЦЕНАРИЙ Б: КАРТА ЕСТЬ -> СПИСЫВАЕМ В 1 КЛИК
    # =====================================================================
    if saved_subscription and saved_subscription.rebill_id:
        # Создаем черновик заказа со статусом RECURRING (повторный)
        new_order = PaymentOrder(
            id=order_id, user_id=user_id, student_id=student_id, club_id=user.club_id,
            amount_kopecks=amount_kopecks, lesson_count=lesson_count, days_to_add=days_to_add,
            status="NEW", type="RECURRING"  # Маркер повторной оплаты
        )
        session.add(new_order)
        await session.commit()

        # Меняем текст на «прогресс-бар», чтобы юзер видел, что магия пошла
        await callback.message.edit_text("⏳ <b>Оплата в 1 клик...</b>\n\nСписываем средства со связанной карты. Пожалуйста, подождите.", parse_mode="HTML")

        ui_cfg = club.club_settings.get("ui", {}) if club.club_settings else {}
        club_name = ui_cfg.get("club_name", club.name if club else "Фитнес-клуб")

        # Вызываем скрытое списание по токену сохраненной карты
        charge_data = await yookassa_node.charge_payment(
            order_id=order_id,
            amount_kopecks=amount_kopecks,
            payment_method_id=saved_subscription.rebill_id,
            club_name=club_name
        )

        if charge_data.get("Success"):
            # Если статус 'succeeded' — ЮKassa списала деньги сразу в фоне!
            if charge_data.get("Status") == "succeeded":
                # Здесь можно сразу выдать сообщение об успехе, 
                # но лучше дождаться вебхука, который начислит абонемент и пришлет уведомление.
                await state.clear()
                return
            else:
                # Если статус pending (например, банк проверяет), просто ждем вебхук
                await state.clear()
                return
        else:
            # Если списание по привязанной карте сорвалось (нет денег, карта просрочена)
            new_order.status = "REJECTED"
            await session.commit()
            
            # Предлагаем оплатить по старинке (ссылкой)
            kb = types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="💳 Ввести данные карты вручную", callback_data="pay_method_official_force_new")]
            ])
            return await callback.message.edit_text(
                f"❌ <b>Не удалось списать оплату со связанной карты.</b>\n\n"
                f"Причина: {charge_data.get('Message', 'Отклонено банком')}.\n"
                f"Вы можете оплатить тариф вручную, введя данные заново:",
                parse_mode="HTML", reply_markup=kb
            )

    # =====================================================================
    # СЦЕНАРИЙ А: КАРТЫ НЕТ -> ГЕНЕРИРУЕМ ССЫЛКУ (Твой стандартный код)
    # =====================================================================
    new_order = PaymentOrder(
        id=order_id, user_id=user_id, student_id=student_id, club_id=user.club_id,
        amount_kopecks=amount_kopecks, lesson_count=lesson_count, days_to_add=days_to_add,
        status="NEW", type="FIRST"
    )
    session.add(new_order)
    await session.commit()

    bot_info = await callback.bot.get_me()
    clean_bot_username = bot_info.username.replace("@", "")

    payment_data = await yookassa_node.init_payment(
        order_id=order_id, amount_kopecks=amount_kopecks, user_id=user_id, bot_username=clean_bot_username
    )

    if payment_data.get("Success"):
        payment_url = payment_data.get("PaymentURL")
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="💳 Перейти к оплате картой", url=payment_url)]
        ])
        await callback.message.edit_text(
            f"💳 <b>Официальная оплата подписки</b>\n\n"
            f"Вы выбрали тариф: <b>{data['tariff_label']} за {price}₽</b>\n\n"
            f"После успешной оплаты ваша карта привяжется к системе для быстрой оплаты в 1 клик.",
            reply_markup=kb, parse_mode="HTML"
        )
        await state.clear()
    else:
        new_order.status = "REJECTED"
        await session.commit()
        await callback.message.answer(f"❌ Ошибка создания платежа: {payment_data.get('Message')}")




@router.message(PaymentStates.waiting_for_receipt, F.photo)
async def handle_receipt_submission(
        message: types.Message,
        state: FSMContext,
        club: Club,
        club_settings: dict
):
    # 1. Сбор данных из стейта плательщика
    data = await state.get_data()
    student_id = data.get('student_id')
    sport_type = data.get('sport_type')
    lesson_count = data.get('lesson_count', 0)  # Будет число (8, 12) или 999 для безлимита
    days_to_add = data.get('days_to_add', 30)   # <--- ДОСТАЛИ настроенные дни (30, 45, 90)
    price = data.get('price', 0)

    photo_id = message.photo[-1].file_id
    user_id = message.from_user.id
    username = f"@{message.from_user.username}" if message.from_user.username else f"ID: {user_id}"

    # 2. Текст уведомления для владельца клуба
    discipline_name = club_settings.get("disciplines", {}).get(sport_type, {}).get("name", "Спорт")

    # Корректно скрываем маркер 999 на экране админа
    tariff_label = "<b>♾ БЕЗЛИМИТ</b>" if lesson_count == 999 else f"<b>🔢 {lesson_count} зан.</b>"

    admin_text = (
        f"📩 <b>НОВЫЙ ЧЕК — {club.name}</b>\n\n"
        f"👤 Отправитель: {username}\n"
        f"🥋 Секция: <b>{discipline_name}</b>\n"
        f"📊 Тариф: {tariff_label} на <b>{days_to_add} дн.</b> за <b>{price}₽</b>\n"
        f"🆔 ID Атлета: <code>{student_id}</code>\n"
    )

    # 3. Компактные кнопки для админа (без текста sport_type, строго цифры!)
    # Формат: adm_confirm_[student_id]_[lesson_count]_[days_to_add]
    admin_kb = InlineKeyboardBuilder()
    admin_kb.row(types.InlineKeyboardButton(
        text="✅ Подтвердить и активировать",
        callback_data=f"adm_confirm_{student_id}_{lesson_count}_{days_to_add}") # <--- Зашили дни!
    )
    admin_kb.row(types.InlineKeyboardButton(
        text="❌ Отклонить чек",
        callback_data=f"adm_decline_{user_id}")
    )

    # 4. Отправка владельцу клуба
    try:
        await message.bot.send_photo(
            chat_id=club.owner_id,
            photo=photo_id,
            caption=admin_text,
            reply_markup=admin_kb.as_markup(),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления админа {club.owner_id}: {e}")
        return await message.answer("⚠️ Ошибка: Администратор клуба временно недоступен. Попробуйте позже.")

    # 5. Ответ пользователю
    support = club_settings.get("ui", {}).get("support_link", "@admin")
    await message.answer(
        f"✅ <b>Чек отправлен в {club.name}!</b>\n\n"
        "Мы проверим его в ближайшее время. Абонемент обновится автоматически.\n\n"
        f"Связь с поддержкой: {support}",
        parse_mode="HTML"
    )

    # Очищаем стейт, чтобы юзер мог свободно нажимать другие кнопки в боте
    await state.clear()


@router.callback_query(F.data.startswith('adm_confirm_'))
async def admin_confirm_payment(
        callback: types.CallbackQuery,
        session: AsyncSession,
        club: Club,
        club_settings: dict
):
    # 1. ЗАЩИТА от двойного клика по кнопке
    if any(word in (callback.message.caption or "") for word in ["✅ ОФОРМЛЕНО", "✅ ОПЛАЧЕНО", "🟢 ОДОБРЕНО"]):
        return await callback.answer("Этот чек уже обработан!", show_alert=True)

    # 2. ПРОВЕРКА ПРАВ ДОСТУПА
    if callback.from_user.id != club.owner_id:
        return await callback.answer("❌ Вы не являетесь владельцем этого клуба!", show_alert=True)

    # 3. ПАРСИНГ данных из кнопки
    # adm_confirm_[student_id]_[lesson_count]_[days_to_add]
    parts = callback.data.split('_')
    try:
        student_id = int(parts[2])
        count = int(parts[3])  # Получит число занятий или 999
        days_to_add = int(parts[4])  # Получит точные дни (30, 45, 90)
    except (IndexError, ValueError):
        return await callback.answer("Ошибка распаковки данных кнопки ❌", show_alert=True)

    # 4. ЛОГИКА ЗАЧИСЛЕНИЯ абонемента в СУБД
    result = await add_abon(
        student_id=student_id,
        lessons_count=count,
        session=session,
        club_id=club.id,
        club_settings=club_settings,
        days_to_add=days_to_add  # <--- ПЕРЕДАЛИ точный срок купленного тарифа!
    )

    if result:
        new_expire, parent_id = result

        # Красивый статус для админского экрана (прячем техническое 999)
        desc = "БЕЗЛИМИТ" if count == 999 else f"{count} зан."

        # 5. UI: Полностью убираем инлайн-кнопки под фоткой чека и пишем статус
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.edit_caption(
            caption=callback.message.caption + f"\n\n🟢 <b>ОДОБРЕНО АДМИНОМ!</b>\n📦 Тариф: {desc}\n📅 Продлен до: {new_expire}",
            parse_mode="HTML"
        )

        # 6. КРАСИВОЕ SaaS-УВЕДОМЛЕНИЕ ДЛЯ КЛИЕНТА (РОДИТЕЛЯ)
        try:
            # Вытаскиваем имя из JSONB
            ui_club_name = club_settings.get("ui", {}).get("club_name")

            # Если имя пустое или совпадает с дефолтной заглушкой — берем железное имя из колонки club.name
            if not ui_club_name or ui_club_name == "Новый фитнес-клуб":
                club_name = club.name
            else:
                club_name = ui_club_name

            await callback.bot.send_message(
                chat_id=parent_id,
                text=f"🥳 <b>Отличные новости!</b>\n\n"
                     f"Ваша оплата в фитнес-клуб <b>{club_name}</b> успешно проверена.\n"
                     f"Абонемент (<b>{desc}</b>) успешно активирован и действует до: <b>{new_expire}</b>. 🔥\n\n"
                     f"<i>Ждем вас на тренировках!</i>",
                parse_mode="HTML"
            )
        except Exception as e:
            # Ловим любые ошибки отправки (блок бота, флуд), чтобы админ-панель не падала
            logger.error(f"Не удалось доставить уведомление пользователю {parent_id}: {e}")

        await callback.answer("Успешно зачислено в базу! ✅")
    else:
        await callback.answer("❌ Ошибка: Не удалось обновить абонемент атлета в БД", show_alert=True)


@router.callback_query(F.data.startswith("adm_decline_"))
async def admin_decline_pay(
        callback: types.CallbackQuery,
        club: Club,
        club_settings: dict  # Подтягиваем настройки из мидлвари
):
    # 1. ЗАЩИТА: проверяем, не обработан ли чек кем-то ранее
    caption = callback.message.caption or ""
    if any(word in caption for word in ["❌ ОТКЛОНЕНО", "🟢 ОДОБРЕНО", "✅ ОФОРМЛЕНО"]):
        return await callback.answer("Этот чек уже был обработан!", show_alert=True)

    # 2. Достаем ID родителя из callback_data
    parent_id = int(callback.data.split("_")[-1])

    # 3. Обновляем сообщение у АДМИНА (УБИРАЕМ КНОПКИ через reply_markup=None)
    await callback.message.edit_caption(
        caption=caption + "\n\n❌ <b>ОТКЛОНЕНО АДМИНИСТРАТОРОМ</b>",
        reply_markup=None,
        parse_mode="HTML"
    )

    # 4. Уведомляем РОДИТЕЛЯ
    try:
        club_name = club_settings.get("ui", {}).get("club_name", club.name)
        support = club_settings.get("ui", {}).get("support_link", "@admin")

        await callback.bot.send_message(
            chat_id=parent_id,
            text=(
                f"❌ <b>Ваш чек на оплату отклонен</b>\n"
                f"📍 Клуб: <b>{club_name}</b>\n\n"
                f"Пожалуйста, проверьте правильность отправленных данных. "
                f"Если возникли вопросы, свяжитесь с администрацией клуба: {support}"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Не удалось отправить отказ юзеру {parent_id}: {e}")

    await callback.answer("Оплата успешно отклонена")


@router.callback_query(F.data == "admin_cash_list")
async def show_all_students_for_cash(
        callback: types.CallbackQuery,
        session: AsyncSession,
        club: Club,
        club_settings: dict
):
    # 1. Фильтруем студентов ТОЛЬКО этого клуба (SaaS-изоляция)
    stmt = select(Student).where(
        Student.club_id == club.id
    ).order_by(Student.name)

    result = await session.execute(stmt)
    students = result.scalars().all()

    if not students:
        club_name = club_settings.get("ui", {}).get("club_name", club.name)
        return await callback.answer(f"В клубе {club_name} пока нет зарегистрированных атлетов", show_alert=True)

    # 2. Собираем клавиатуру с красивым скрытием маркера 999
    builder = InlineKeyboardBuilder()
    for s in students:
        balance = s.balance_lessons or 0
        # Если баланс равен 999 — выводим "безлимит", иначе пишем число занятий
        balance_label = "безлимит" if balance == 999 else f"{balance} зан."

        builder.row(InlineKeyboardButton(
            text=f"👤 {s.name} ({balance_label})",
            callback_data=f"cash_pay_{s.id}")
        )

    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_keyboard"))

    club_name = club_settings.get("ui", {}).get("club_name", club.name)
    await callback.message.edit_text(
        f"📍 Клуб: <b>{club_name}</b>\n"
        "Выберите атлета для внесения <b>наличных</b>:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cash_pay_"))
async def process_cash_payment(
        callback: types.CallbackQuery,
        state: FSMContext,
        session: AsyncSession,
        club_settings: dict,
        club: Club
):
    # Достаем ID студента из callback_data кнопки общего списка
    student_id = int(callback.data.split("_")[-1])

    # Вытаскиваем все дисциплины, которые настроены для этого конкретного клуба
    disciplines = club_settings.get("disciplines", {})

    if not disciplines:
        return await callback.answer("⚠️ В настройках вашего клуба еще не добавлено ни одного направления!",
                                     show_alert=True)

    # Забираем текущий стейт (если админ пришел по какому-то длинному пути, где секция уже была выбрана)
    data = await state.get_data()
    sport_type = data.get('sport_type')

    # ЕСЛИ СЕКЦИЯ ЕЩЕ НЕ ВЫБРАНА (наш случай с общим списком):
    if not sport_type:
        # Вариант А: В клубе настроена только одна единственная секция
        if len(disciplines) == 1:
            sport_type = list(disciplines.keys())[0]
            await state.update_data(sport_type=sport_type)  # Сохраняем её автоматический выбор

        # Вариант Б: В клубе несколько разных секций. Показываем клавиатуру выбора направления
        else:
            builder = InlineKeyboardBuilder()
            for d_code, d_data in disciplines.items():
                builder.row(types.InlineKeyboardButton(
                    text=f"🥋 {d_data.get('name', d_code)}",
                    # При клике перезапустим этот же хендлер, но уже сохранив стейт!
                    callback_data=f"select_sport_for_cash_{student_id}_{d_code}"
                ))
            builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_cash_list"))

            return await callback.message.edit_text(
                "🎯 У этого атлета можно активировать разные направления.\n"
                "<b>Выберите секцию для внесения оплаты:</b>",
                reply_markup=builder.as_markup(),
                parse_mode="HTML"
            )

    # Ищем конфигурацию выбранной дисциплины
    disc_cfg = disciplines.get(sport_type, {})

    if not disc_cfg:
        return await callback.answer(f"Ошибка: Направление '{sport_type}' отсутствует в настройках 🛠", show_alert=True)

    # Сохраняем все маркеры для финального хендлера подтверждения cash_confirm_
    await state.update_data(
        cash_student_id=student_id,
        cash_sport_type=sport_type
    )

    # Выводим клавиатуру тарифов этой секции
    await callback.message.edit_text(
        f"💰 <b>Прием наличных: {disc_cfg.get('name')}</b>\n"
        f"Выберите тарифный план, который оплатил атлет:",
        reply_markup=get_cash_options_kb(disc_cfg),
        parse_mode="HTML"
    )
    await callback.answer()


# МИНИ-ХЕНДЛЕР: ловит выбор секции, если в клубе их несколько
# МИНИ-ХЕНДЛЕР: ловит выбор секции, если в клубе их несколько
@router.callback_query(F.data.startswith("select_sport_for_cash_"))
async def select_sport_for_cash_callback(
        callback: types.CallbackQuery,
        state: FSMContext,
        session: AsyncSession,
        club_settings: dict,
        club: Club
):
    # Безопасно разбираем входящие данные из callback.data
    parts = callback.data.split("_")
    student_id = int(parts[-2])
    sport_type = parts[-1]

    # 1. Записываем выбор направления и ID в стейт для финальной оплаты
    await state.update_data(
        sport_type=sport_type,
        cash_student_id=student_id,
        cash_sport_type=sport_type
    )

    # 2. Достаем конфигурацию выбранной дисциплины из настроек
    disciplines = club_settings.get("disciplines", {})
    disc_cfg = disciplines.get(sport_type, {})

    if not disc_cfg:
        return await callback.answer(f"Ошибка: Направление '{sport_type}' не найдено 🛠", show_alert=True)

    # 3. Напрямую выводим клавиатуру тарифов (БЕЗ мутации callback.data и вызова других хендлеров!)
    try:
        await callback.message.edit_text(
            f"💰 <b>Прием наличных: {disc_cfg.get('name')}</b>\n"
            f"Выберите тарифный план, который оплатил атлет:",
            reply_markup=get_cash_options_kb(disc_cfg), # Генерирует тарифы по индексам
            parse_mode="HTML"
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"❌ Ошибка вывода тарифов в мини-хендлере: {e}")
        await callback.answer("Ошибка генерации клавиатуры тарифов ❌", show_alert=True)


@router.callback_query(F.data.startswith("confirm_cash_"))
async def final_cash_pay(
        callback: types.CallbackQuery,
        state: FSMContext,
        session: AsyncSession,
        club: Club,
        club_settings: dict
):
    # 1. Забираем сохраненные данные атлета и секции из стейта
    data = await state.get_data()
    student_id = data.get('cash_student_id')
    sport_type = data.get('cash_sport_type')  # Достаем код секции (boxing, bjj и т.д.)

    # 2. Получаем ИНДЕКС тарифа из callback_data новой клавиатуры (0, 1, 2...)
    tariff_idx = int(callback.data.split("_")[-1])

    # 3. Достаем конфигурацию секции и массив её тарифов
    disc_cfg = club_settings.get("disciplines", {}).get(sport_type, {})
    tariffs = disc_cfg.get("tariffs", [])

    if tariff_idx >= len(tariffs):
        return await callback.answer("Ошибка: выбранный тариф не найден в настройках клуба ❌", show_alert=True)

    # Берем конкретный тарифный план из списка по его индексу
    selected_tariff = tariffs[tariff_idx]

    # Извлекаем реальные параметры, заданные админом
    count = selected_tariff.get("count", 0)  # Сюда прилетит число занятий (8, 12) или 999 для безлимита
    days = selected_tariff.get("days", 30)  # Точный срок действия абонемента из тарифа

    # 4. Вызываем обновленную функцию начисления, передавая точные дни
    result = await add_abon(
        student_id=student_id,
        lessons_count=count,
        session=session,
        club_id=club.id,
        club_settings=club_settings,
        days_to_add=days  # <--- Теперь срок действия будет начислен абсолютно верно!
    )

    if result:
        new_expire, parent_id = result

        # Формируем красивое отображение тарифа для экрана админа (прячем техническое 999)
        t_label = f"Безлимит ({days} дн.)" if count == 999 else f"{count} зан. ({days} дн.)"

        await callback.message.edit_text(
            f"✅ <b>Наличные успешно зачислены!</b>\n\n"
            f"Направление: <b>{disc_cfg.get('name')}</b>\n"
            f"Тариф: <b>{t_label}</b>\n"
            f"Абонемент продлен до: <b>{new_expire}</b>",
            parse_mode="HTML"
        )

        # Автоматически отправляем красивое уведомление родителю/спортсмену
        if parent_id:
            try:
                club_name = club_settings.get("ui", {}).get("club_name", club.name)
                await callback.bot.send_message(
                    chat_id=parent_id,
                    text=f"💵 <b>Оплата наличными подтверждена!</b>\n\n"
                         f"🏛 Клуб: <b>{club_name}</b>\n"
                         f"🥋 Направление: {disc_cfg.get('name')}\n"
                         f"📅 Абонемент успешно активирован до: <b>{new_expire}</b>.",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления родителю {parent_id}: {e}")

        # Полностью очищаем стейт админа после успешного платежа
        await state.clear()
    else:
        await callback.answer("Ошибка при обновлении данных студента в БД ❌", show_alert=True)


@router.callback_query(F.data.startswith("buy_sub_"))
async def send_subscription_invoice(callback: types.CallbackQuery):
    days = int(callback.data.split("_")[-1])

    # Цены: для 30 дней - 500 звезд, для 365 - 5000
    price_amount = 500 if days == 30 else 5000

    await callback.message.answer_invoice(
        title=f"Подписка на {days} дней",
        description=f"Продление доступа к SaaS платформе для клуба",
        prices=[LabeledPrice(label="XTR", amount=price_amount)],
        provider_token="",  # Для Stars токен пустой
        payload=f"sub_{days}",
        currency="XTR"
    )
    await callback.answer()


@router.callback_query(F.data == "pay_menu")
async def show_pay_menu(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()

    # Создаем кнопки с правильными префиксами buy_sub_
    builder.row(types.InlineKeyboardButton(
        text="🌙 1 месяц — 500 ⭐", callback_data="buy_sub_30")
    )
    builder.row(types.InlineKeyboardButton(
        text="☀️ 1 год — 5000 ⭐", callback_data="buy_sub_365")
    )
    builder.row(types.InlineKeyboardButton(
        text="⬅️ Назад", callback_data="admin")  # Если есть главное меню
    )

    await callback.message.edit_text(
        "<b>Выберите тарифный план:</b>\n\n"
        "Оплата производится через Telegram Stars. Подписка активируется мгновенно.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()


# 2. Обязательный ответ на pre_checkout_query (ТГ ждет его 10 сек)
@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)


# 3. Финальный этап: зачисление подписки после успешной оплаты


@router.message(F.successful_payment)
async def on_successful_payment(message: Message, session: AsyncSession, club: Club, redis: Redis):
    payload = message.successful_payment.invoice_payload
    days = int(payload.split("_")[-1])

    now = datetime.now()

    # Считаем новую дату окончания подписки для клуба
    if club.subscription_expire_at and club.subscription_expire_at > now:
        new_expire = club.subscription_expire_at + timedelta(days=days)
    else:
        new_expire = now + timedelta(days=days)

    # ВАЖНО: Принудительно пишем в базу данных через update
    await session.execute(
        update(Club)
        .where(Club.id == club.id)
        .values(subscription_expire_at=new_expire)
    )
    await session.commit()

    # Сбрасываем кэш Redis (у вас это сделано идеально)
    cache_key = f"club_config:{message.bot.token}"
    await redis.delete(cache_key)
    logger.info(f"Кэш сброшен для бота: {message.bot.token}")

    await message.answer(
        f"✅ <b>Оплата прошла успешно!</b>\n"
        f"Доступ к платформе продлен на <b>{days} дней</b>.\n"
        f"Новая дата окончания подписки: <b>{new_expire.strftime('%d.%m.%Y')}</b>",
        parse_mode="HTML"
    )


@router.callback_query(F.data == 'manage_subscription')
async def process_manage_subscription(callback: types.CallbackQuery, session: AsyncSession):
    """Главный экран управления подпиской: проверка привязанной карты"""
    await callback.answer()
    user_id = callback.from_user.id

    # 1. Ищем активную подписку пользователя с сохраненной картой
    # (Проверяем поле rebill_id на наличие токена карты ЮKassa)
    query = select(Subscription).where(
        Subscription.user_id == user_id,
        Subscription.rebill_id.isnot(None),
        Subscription.is_active == True
    )
    result = await session.execute(query)
    subscriptions = result.scalars().all()

    if not subscriptions:
        # Если привязанных карт в базе нет
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔙 Назад в профиль", callback_data="profile")] # Замени коллбэк назад на свой, если нужно
        ])
        return await callback.message.edit_text(
            "💳 <b>Управление подпиской</b>\n\n"
            "У вас нет сохраненных карт в системе автопродления.\n"
            "Карта привязывается автоматически после первой успешной онлайн-оплаты абонемента.",
            parse_mode="HTML",
            reply_markup=kb
        )

    # Если нашли сохраненные карты (берем первую для простоты, так как у ученика обычно одна карта)
    sub = subscriptions[0]
    
    # Формируем текст с датой следующего списания, если бы крон работал (для информирования)
    next_charge_str = sub.next_charge_at.strftime("%d.%m.%Y") if sub.next_charge_at else "Не определено"

    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="❌ ОТВЯЗАТЬ КАРТУ", callback_data="confirm_delete_card")],
        [types.InlineKeyboardButton(text="🔙 Назад в профиль", callback_data="profile")]
    ])

    await callback.message.edit_text(
        f"💳 <b>УПРАВЛЕНИЕ ПОДПИСКОЙ</b>\n\n"
        f" К вашему профилю привязана банковская карта для быстрой оплаты.\n\n"
        f"<b>Статус автопродления:</b> АКТИВЕН\n"
        f"<b>Дата следующего расчетного периода:</b> {next_charge_str}\n\n"
        f"Вы можете отвязать карту в любой момент. После отвязки вам придется вводить данные карты вручную при следующей покупке.",
        parse_mode="HTML",
        reply_markup=kb
    )


@router.callback_query(F.data == 'confirm_delete_card')
async def process_confirm_delete_card(callback: types.CallbackQuery):
    """Экран-предохранитель: подтверждение отвязки карты через галочку и крестик"""
    await callback.answer()

    # Создаем клавиатуру с галочкой и крестиком
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="✅ Да, отвязать", callback_data="execute_delete_card"),
            types.InlineKeyboardButton(text="❌ Нет, отмена", callback_data="manage_subscription")
        ]
    ])

    await callback.message.edit_text(
        "⚠️ <b>Вы уверены, что хотите отвязать карту?</b>\n\n"
        "Вы больше не сможете оплачивать абонементы клуба в один клик.",
        parse_mode="HTML",
        reply_markup=kb
    )


@router.callback_query(F.data == 'execute_delete_card')
async def process_execute_delete_card(callback: types.CallbackQuery, session: AsyncSession):
    """Физическое удаление токена карты из Postgres на Аэзе"""
    await callback.answer("Карта успешно удалена!", show_alert=True)
    user_id = callback.from_user.id

    # Блокируем строки подписок пользователя для безопасного апдейта
    query = select(Subscription).where(
        Subscription.user_id == user_id,
        Subscription.rebill_id.isnot(None)
    ).with_for_update()
    
    result = await session.execute(query)
    subscriptions = result.scalars().all()

    if subscriptions:
        for sub in subscriptions:
            # Стираем rebill_id (токен ЮKassa), чтобы стереть привязку навсегда
            sub.rebill_id = None
            sub.is_active = False # Выключаем флаг подписки
        
        await session.commit()
        logger.info(f"🗑️ Пользователь {user_id} полностью удалил свои банковские карты из СУБД.")

    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🔙 Вернуться в профиль", callback_data="profile")]
    ])

    await callback.message.edit_text(
        "✅ <b>Карта успешно удалена!</b>\n\n"
        "Ваши платежные данные полностью стерты из системы клуба.\n"
        "Автопродление отключено.",
        parse_mode="HTML",
        reply_markup=kb)

