from datetime import date
from aiogram.utils.keyboard import InlineKeyboardBuilder
from services.yookassa_client import YooKassaClient
import uuid
from config import PROXY_URL
from database.db import PaymentOrder, User, StudentParent
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger
from database.db import Student, Club,Subscription 
from handlers.buttons import discipline, get_pay_options_kb, get_cash_options_kb
from database.db import add_abon, purchase_student_freeze
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton
from aiogram import Router, F, types
from handlers.states import PaymentStates
from redis.asyncio import Redis
from services.abuse_guard import rate_limit, audit_block
from services.audit import audit_event
from services.order_notifications import build_owner_receipt_text, format_order_items, resolve_user_label
from services.payment_requisites import get_payment_info_text
from services.availability import payment_availability


router = Router()


@router.message(PaymentStates.waiting_for_freeze_days)
async def receive_paid_freeze_days(message: types.Message, state: FSMContext):
    try:
        days = int((message.text or "").strip())
    except ValueError:
        return await message.answer("Введите целое количество дней, например 14.")
    if not 1 <= days <= 365:
        return await message.answer("Количество дней должно быть от 1 до 365.")
    data = await state.get_data()
    price = float(data.get("freeze_price_per_day", 0))
    if price <= 0:
        await state.clear()
        return await message.answer("Покупка заморозки сейчас недоступна.")
    total = round(price * days, 2)
    await state.update_data(lesson_count=0, days_to_add=days, price=total,
                           discipline_name="Заморозка абонемента", tariff_label=f"Заморозка на {days} дн.")
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="💳 Оплатить картой", callback_data="pay_method_official")],
        [types.InlineKeyboardButton(text="↔️ СБП через YooKassa", callback_data="pay_method_sbp_yookassa")],
        [types.InlineKeyboardButton(text="↩️ Перевод на карту (вручную)", callback_data="pay_method_sbp")],
        [types.InlineKeyboardButton(text="❌ Отмена", callback_data="profile")]
    ])
    await message.answer(f"❄️ Заморозка на <b>{days} дней</b>\nК оплате: <b>{total:g} ₽</b>", reply_markup=kb, parse_mode="HTML")


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
async def select_athlete_handler(
    callback: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    club_id: int  # 🌟 Из нашей оптимизированной мидлвари! Без этого нельзя!
):
    discipline_code = callback.data.split('_')[1]  # boxing, kickboxing и т.д.
    await state.update_data(sport_type=discipline_code)

    # 🌟 ФИКС: Жестко ищем детей только этого родителя И строго в рамках текущего КЛУБА
    res = await session.execute(
        select(Student)
        .outerjoin(StudentParent, StudentParent.student_id == Student.id)
        .where(or_(Student.parent_id == callback.from_user.id, StudentParent.parent_id == callback.from_user.id))
        .where(Student.club_id == club_id)  # <--- Защита от смешивания данных разных клубов
    )
    students = res.scalars().all()

    # ПОДСТРАХОВКА: Если детей в базе еще нет
    if not students:
        return await callback.answer(
            "🙋‍♂️ У вас еще не зарегистрировано ни одного атлета в этом клубе!\n"
            "Пожалуйста, сначала добавьте ребенка в личном кабинете.",
            show_alert=True
        )

    # Генерируем клавиатуру ТОЛЬКО из реальных детей
    kb = InlineKeyboardBuilder()
    for s in students:
        # 🌟 СТРАХОВКА: Чтобы не превысить лимит 64 байта в callback_data,
        # если имя дисциплины короткое (box, kick, bjj, yoga), можно зашить его в кнопку.
        # Формат: set_at_[student_id]_[short_discipline]
        # Если боитесь за лимит байт, оставляем просто s.id, но фильтр по club_id выше — ОБЯЗАТЕЛЕН.
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
    parts = callback.data.split('_')
    target = parts[2]
    student_id = callback.from_user.id if target == "me" else int(target)

    # 🌟 ФИКС: Гарантированно вытаскиваем дисциплину из кнопки (если есть) или из FSM стейта
    if len(parts) > 3:
        sport_type = parts[3]
    else:
        data = await state.get_data()
        sport_type = data.get('sport_type')

    # Жесткая подстраховка: если дисциплину вообще нигде не нашли, вежливо возвращаем к началу
    if not sport_type:
        return await callback.answer(
            "⚠️ Сессия устарела. Пожалуйста, выберите секцию заново.",
            show_alert=True
        )

    # Жестко фиксируем в стейт оба параметра — и студента, и спорт
    await state.update_data(student_id=student_id, sport_type=sport_type)

    # Спокойно берем конфиг дисциплины из настроек клуба
    discipline_cfg = club_settings.get("disciplines", {}).get(sport_type)

    if not discipline_cfg:
        return await callback.answer("❌ Ошибка: Выбранное направление временно недоступно.", show_alert=True)

    # Формируем клавиатуру тарифов (8 зан, 12 зан, безлимит)
    markup = get_pay_options_kb(discipline_cfg, sport_type)

    await callback.message.edit_text(
        text=f"🥋 Секция: <b>{discipline_cfg.get('name', 'Спорт')}</b>\n"
             f"Выбранный атлет: {(await state.get_data()).get('student_name', 'Ребенок')}\n\n"
             f"Пожалуйста, выберите подходящий тарифный план 👇",
        reply_markup=markup,
        parse_mode="HTML"
    )
    await callback.answer()


# Полностью заменяем хендлер set_limit_ на этот:
# Заменяем старый хендлер set_tariff_ на этот
@router.callback_query(F.data.startswith('set_tariff_'))
async def process_kids_limit(
        callback: types.CallbackQuery,
        state: FSMContext,
        session: AsyncSession,
        club_settings: dict
):
    parts = callback.data.split('_')
    try:
        sport_type = parts[2]
        tariff_idx = int(parts[3])
    except (IndexError, ValueError):
        return await callback.answer("Ошибка обработки кнопки тарифа ❌", show_alert=True)
    # 1. Извлекаем данные о выбранной секции и тарифе
    discipline_cfg = club_settings.get("disciplines", {}).get(sport_type, {})
    if not discipline_cfg:
        return await callback.answer("Ошибка: секция не найдена 🛠", show_alert=True)
    tariffs = discipline_cfg.get("tariffs", [])
    if tariff_idx >= len(tariffs):
        return await callback.answer("Ошибка: выбранный тариф больше не существует ❌", show_alert=True)
    selected_tariff = tariffs[tariff_idx]
    # =========================================================================
    # 🔥 ЗДЕСЬ МЫ ЖЕСТКО И ВЕЖЛИВО ПОБЕЖДАЕМ КЛИЕНТОВ ПО ВОЗРАСТУ
    # =========================================================================
    # Достаем ID ребенка, которого родитель выбрал на предыдущем шаге из памяти FSM
    data = await state.get_data()
    student_id = data.get('student_id')
    if student_id:
        # Тянем актуальные данные ребенка из Postgres
        student_res = await session.execute(select(Student).where(Student.id == student_id))
        student = student_res.scalar_one_or_none()
        if student and student.birthday:
            today = date.today()
            # Честный расчет возраста ребенка в годах с учетом месяца и дня рождения
            student_age = today.year - student.birthday.year - (
                        (today.month, today.day) < (student.birthday.month, student.birthday.day))
            # Достаем возрастной лимит из выбранного тарифа
            min_age_limit = selected_tariff.get("min_age", 0)
            # Перехват: если ребенок слишком мал для этого тарифа
            if student_age < min_age_limit:
                await callback.answer()  # Сразу гасим часики на кнопке
                # 🧠 УМНЫЙ СУПЕР-ПОДБОР: Сканируем весь JSONB-конфиг клуба на предмет альтернатив
                available_disciplines = []
                for disc_key, disc_val in club_settings.get("disciplines", {}).items():
                    # Проверяем только активные направления и исключаем то, куда ребенок не прошел
                    if disc_val.get("active") and disc_key != sport_type:
                        sect_tariffs = disc_val.get("tariffs", [])
                        if sect_tariffs:
                            # Проверяем самый первый тариф в альтернативной секции — подходит ли возраст?
                            if student_age >= sect_tariffs[0].get("min_age", 0):
                                available_disciplines.append(f"• <b>{disc_val.get('name')}</b>")
                # Формируем вежливый и аргументированный текст отказа
                alt_text = ""
                if available_disciplines:
                    alt_text = (
                            f"\n\n Заботясь о развитии и безопасности вашего ребенка, мы подобрали "
                            f"альтернативные направления в нашем клубе, куда <b>{student.name}</b> "
                            f"может записаться прямо сейчас:\n" + "\n".join(available_disciplines)
                    )
                else:
                    alt_text = f"\n\nК сожалению, для возраста вашего ребенка в нашем клубе пока нет подходящих открытых направлений."
                # Перерисовываем экран в красивую заглушку-рекомендацию
                kb = types.InlineKeyboardMarkup(inline_keyboard=[[
                    types.InlineKeyboardButton(text="↩️ Вернуться к выбору направлений", callback_data="choose_section")
                ]])
                return await callback.message.edit_text(
                    text=f"⚠️ <b>Доступ ограничен по возрасту!</b>\n\n"
                         f"В целях эффективного развития спортивных навыков и соблюдения техники безопасности, "
                         f"на тарифный план секции <b>{discipline_cfg.get('name')}</b> принимаются дети строго с <b>{min_age_limit} лет</b>.\n\n"
                         f"Сейчас вашему атлету <b>{student.name}</b> исполнилось <b>{student_age} лет</b>."
                         f"{alt_text}\n\n"
                         f"Вы можете выбрать другое доступное направление по кнопке ниже 👇",
                    reply_markup=kb,
                    parse_mode="HTML"
                )
    # =========================================================================
    # ЕСЛИ РЕБЕНОК ПРОШЕЛ ПРОВЕРКУ — ВЫПОЛНЯЕТСЯ ТВОЙ СТАНДАРТНЫЙ КОД ОПЛАТЫ
    # =========================================================================
    await state.update_data(sport_type=sport_type)
    price = selected_tariff.get('price')
    days = selected_tariff.get('days', 30)
    count = selected_tariff.get('count', 0)
    display_name = discipline_cfg.get('name', 'Секция')
    label = f"Безлимит на {days} дней" if count == 999 else f"{count} зан. / {days} дн."
    await state.update_data(
        student_id=student_id or callback.from_user.id,
        lesson_count=count,
        days_to_add=days,
        price=price,
        discipline_name=display_name,
        tariff_label=label
    )
    # === НАЧАЛО ТВОЕЙ СТАРОЙ ЛОГИКИ ОПЛАТЫ С ЖЕСТКОЙ ЗАЩИТОЙ ОТ ЗАВИСАНИЯ ===
    try:
        # 🔍 ПРОВЕРКА КАРТЫ РОДИТЕЛЯ (Оборачиваем в try, если таблицы Subscription нет)
        from database.db import Subscription
        sub_res = await session.execute(
            select(Subscription)
            .where(Subscription.user_id == callback.from_user.id)
            .where(Subscription.rebill_id.is_not(None))
            .limit(1)
        )
        saved_card = sub_res.scalar_one_or_none()
    except Exception as sub_error:
        # Если таблицы нет или SQL упал — не вешаем кнопку, а пишем в логи и считаем, что карты нет
        logger.error(f"⚠️ Ошибка проверки сохраненной карты: {sub_error}")
        saved_card = None

    # Формируем динамические кнопки
    inline_keyboard = []

    if saved_card:
        inline_keyboard.append([
            types.InlineKeyboardButton(text="⚡️ Оплатить сохраненной картой", callback_data="pay_one_click")
        ])
        inline_keyboard.append([
            types.InlineKeyboardButton(text="💳 Оплатить новой картой", callback_data="pay_method_official")
        ])
    else:
        inline_keyboard.append([
            types.InlineKeyboardButton(text="💳 Онлайн оплата картой", callback_data="pay_method_official")
        ])

    inline_keyboard.append([
        types.InlineKeyboardButton(text="↔️ СБП через YooKassa", callback_data="pay_method_sbp_yookassa")
    ])

    inline_keyboard.append([
        types.InlineKeyboardButton(text="↩️ Перевод на карту (вручную)", callback_data="pay_method_sbp")
    ])

    online_enabled = payment_availability(club_settings)["online"]
    if not online_enabled:
        inline_keyboard = [row for row in inline_keyboard if not any(button.callback_data in {"pay_one_click", "pay_method_official", "pay_method_official_force_new", "pay_method_sbp_yookassa"} for button in row)]
    kb = types.InlineKeyboardMarkup(inline_keyboard=inline_keyboard)

    text = (
        f"🎯 <b>Выбран тариф: {display_name}</b>\n"
        f"Условия: <b>{label} — {price}₽</b>\n\n"
        f"Пожалуйста, выберите способ оплаты:"
    )

    try:
        await callback.message.edit_text(text=text, reply_markup=kb, parse_mode="HTML")
        await callback.answer()  # 🔥 ОБЯЗАТЕЛЬНО ТУТ ГАСИМ КРУТИЛКУ
    except Exception as edit_error:
        logger.error(f"❌ Ошибка отправки текста оплаты: {edit_error}")
        # Если edit_text отвалился (например, текст совпадает), принудительно отжимаем часики!
        await callback.answer("Ошибка вывода меню оплаты", show_alert=True)


@router.callback_query(F.data == 'pay_one_click')
async def process_one_click_payment(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession,
                                    club: Club, redis: Redis):
    await callback.answer("Провожу платеж в 1 клик...", show_alert=False)

    data = await state.get_data()
    user_id = callback.from_user.id

    # Извлекаем тип спорта (дисциплину) из стейта
    sport_type = data.get('sport_type')  # Сюда прилетит: 'boxing', 'kickboxing', 'bjj', 'yoga'
    idem_key = f"idem:bot:pay_one_click:{club.id}:{user_id}:{data.get('student_id')}:{sport_type}:{data.get('lesson_count')}:{data.get('days_to_add')}"
    if not await rate_limit(redis, idem_key, 1, 90):
        await audit_block("bot_checkout_blocked", "duplicate_one_click", club_id=club.id, user_id=user_id, student_id=data.get("student_id"))
        return await callback.message.answer("Платеж уже создается. Подождите немного.")

    order_id = f"ONE_{uuid.uuid4().hex[:12].upper()}"
    amount_kopecks = int(float(data['price']) * 100)

    user_res = await session.execute(select(User).where(User.user_id == user_id))
    user = user_res.scalar_one_or_none()

    if not user:
        return await callback.message.answer("❌ Ошибка: Пользователь не найден в системе.")

    # Совместимость со старыми пользователями: сначала используем их привязку,
    # а если она не заполнена — клуб текущего бота из middleware.
    club_id = getattr(club, "id", None)
    if not club_id:
        return await callback.message.answer("❌ Ошибка: Не удалось определить ваш клуб.")
    if not club_id:
        return await callback.message.answer("❌ Ошибка: Не удалось определить ваш клуб.")

    # Ищем карту только после определения клуба, чтобы не взять подписку
    # пользователя из другого клуба.
    sub_res = await session.execute(
        select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.club_id == club_id,
            Subscription.rebill_id.is_not(None)
        ).limit(1)
    )
    saved_card = sub_res.scalar_one_or_none()
    if not saved_card:
        return await callback.message.answer("❌ Ошибка: Сохраненная карта не найдена. Оплатите заново для привязки.")

    pay_settings = (club.club_settings or {}).get("payments", {})
    shop_id = pay_settings.get("yookassa_shop_id")
    secret_key = pay_settings.get("yookassa_secret_key")

    if not shop_id or not secret_key:
        return await callback.message.answer("❌ Клуб еще не настроил онлайн-платежи. Оплата в 1 клик невозможна.")

    # Фиксируем заказ со статусом NEW и типом RECURRENT
    new_order = PaymentOrder(
        id=order_id,
        user_id=user_id,
        student_id=data['student_id'],
        club_id=club_id,
        amount_kopecks=amount_kopecks,
        lesson_count=data['lesson_count'],
        days_to_add=data['days_to_add'],
        status="NEW",
        type="RECURRENT"
    )
    session.add(new_order)
    await session.commit()

    # 3. Вызываем фоновое списание по токену карты через ЮKassa
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

        # 🌟 КРИТИЧЕСКИЙ ФИКС: Передаем discipline в нашу обновленную функцию add_abon
        abon_result = await add_abon(
            student_id=data['student_id'],
            lessons_count=data['lesson_count'],
            session=session,
            club_id=club_id,
            club_settings=club.club_settings,
            days_to_add=data['days_to_add'],
            discipline=sport_type  # <--- ТЕПЕРЬ ДИСЦИПЛИНА ЗАПИШЕТСЯ КОРРЕКТНО!
        )
        await session.commit()

        if abon_result:
            new_expire, _ = abon_result
            desc = "БЕЗЛИМИТ" if data['lesson_count'] == 999 else f"{data['lesson_count']} зан."

            # Достаем понятное человеку название секции для UI
            human_disc = (club.club_settings or {}).get("disciplines", {}).get(str(sport_type).lower(), {}).get("name",
                                                                                                              sport_type)

            await callback.message.edit_text(
                f"⚡️ <b>Оплата успешно проведена в 1 клик!</b>\n\n"
                f"🥋 Секция: <b>{human_disc}</b>\n"
                f"💳 Списано: <b>{data['price']}₽</b> с вашей сохраненной карты.\n"
                f"📦 Абонемент (<b>{desc}</b>) успешно активирован и действует до: <b>{new_expire}</b>. 🔥"
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
        club_settings: dict,
        redis: Redis
):
    """Сценарий ручного перевода на карту (старая схема по чеку)"""
    # Включаем твой родной стейт ожидания чека
    await state.set_state(PaymentStates.waiting_for_receipt)

    # Достаем сохраненные на прошлом шаге данные из FSM
    data = await state.get_data()

    # UI часть (Вывод реквизитов для перевода из конфига клуба)
    payment_info = get_payment_info_text(club_settings)
    user_id = callback.from_user.id
    if not await rate_limit(redis, f"rl:bot:sbp:{user_id}", 3, 60):
        await audit_block("bot_flow_blocked", "sbp_rate_limited", user_id=user_id)
        return await callback.answer("Слишком часто. Попробуйте позже.", show_alert=True)

    text = (
        f"💰 <b>Перевод на карту: {data['discipline_name']}</b>\n"
        f"Тариф: <b>{data['tariff_label']} — {data['price']}₽</b>\n\n"
        f"💳 <b>Реквизиты для перевода на карту:</b>\n"
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


@router.callback_query(F.data == 'pay_method_sbp_yookassa')
async def process_sbp_yookassa_payment(
        callback: types.CallbackQuery,
        state: FSMContext,
        session: AsyncSession,
        club: Club,
        redis: Redis,
):
    """Онлайн-оплата через СБП в YooKassa."""
    await callback.answer("Проверяю СБП...", show_alert=False)

    data = await state.get_data()
    user_id = callback.from_user.id
    student_id = data.get("student_id")
    price = data.get("price")
    amount_kopecks = int(float(price) * 100)
    lesson_count = data.get("lesson_count")
    days_to_add = data.get("days_to_add")
    sport_type = data.get("sport_type")
    payment_kind = data.get("payment_kind", "SUBSCRIPTION")

    idem_key = f"idem:bot:pay_sbp:{club.id}:{user_id}:{student_id}:{sport_type}:{lesson_count}:{days_to_add}:{payment_kind}"
    if not await rate_limit(redis, idem_key, 1, 90):
        await audit_block("bot_checkout_blocked", "duplicate_sbp_payment", club_id=club.id, user_id=user_id, student_id=student_id)
        return await callback.answer("Платеж уже создается. Подождите немного.", show_alert=True)

    user_res = await session.execute(select(User).where(User.user_id == user_id))
    user = user_res.scalar_one_or_none()
    if not user:
        return await callback.message.answer("❌ Ошибка: Пользователь не найден в системе.")

    club_id = getattr(club, "id", None)
    if not club_id:
        return await callback.message.answer("❌ Ошибка: Не удалось определить ваш клуб.")
    if not club_id:
        return await callback.message.answer("❌ Ошибка: Не удалось определить ваш клуб.")

    pay_settings = (club.club_settings or {}).get("payments", {})
    shop_id = pay_settings.get("yookassa_shop_id")
    secret_key = pay_settings.get("yookassa_secret_key")
    sbp_enabled = pay_settings.get("yookassa_sbp_enabled", True)

    if not sbp_enabled:
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="💳 Оплатить картой", callback_data="pay_method_official")],
            [types.InlineKeyboardButton(text="↩️ Перевод на карту (вручную)", callback_data="pay_method_sbp")],
        ])
        await callback.message.edit_text(
            "⚠️ <b>СБП через YooKassa отключена в настройках клуба.</b>\n\n"
            "Выберите другой способ оплаты ниже.",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return await callback.answer("СБП отключена для этого клуба", show_alert=True)

    if not shop_id or not secret_key:
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="💳 Оплатить картой", callback_data="pay_method_official")],
            [types.InlineKeyboardButton(text="↩️ Перевод на карту (вручную)", callback_data="pay_method_sbp")],
        ])
        await callback.message.edit_text(
            "⚠️ <b>Онлайн-СБП пока недоступна.</b>\n\n"
            "В клубе не настроены реквизиты YooKassa для этого способа оплаты.\n"
            "Попробуйте другой вариант:",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return await callback.answer("Онлайн-СБП не настроена", show_alert=True)

    order_id = f"SBP_{uuid.uuid4().hex[:12].upper()}"
    order_type = "FREEZE_SBP" if payment_kind == "FREEZE" else "FIRST_SBP"
    new_order = PaymentOrder(
        id=order_id,
        user_id=user_id,
        student_id=student_id,
        club_id=club_id,
        amount_kopecks=amount_kopecks,
        lesson_count=lesson_count,
        days_to_add=days_to_add,
        status="NEW",
        type=order_type,
        discipline=sport_type,
    )
    session.add(new_order)
    await session.commit()

    yookassa_node = YooKassaClient(shop_id=shop_id, secret_key=secret_key, proxy_url=PROXY_URL)
    bot_info = await callback.bot.get_me()
    clean_bot_username = bot_info.username.replace("@", "")
    payment_data = await yookassa_node.init_payment(
        order_id=order_id,
        amount_kopecks=amount_kopecks,
        user_id=user_id,
        bot_username=clean_bot_username,
        payment_method_type="sbp",
    )

    if payment_data.get("Success"):
        payment_url = payment_data.get("PaymentURL")
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="↔️ Перейти к оплате через СБП", url=payment_url)],
            [types.InlineKeyboardButton(text="💳 Оплатить картой", callback_data="pay_method_official")],
            [types.InlineKeyboardButton(text="↩️ Перевод на карту (вручную)", callback_data="pay_method_sbp")],
        ])
        await callback.message.edit_text(
            f"↔️ <b>Оплата через СБП</b>\n\n"
            f"Вы выбрали тариф: <b>{data['tariff_label']} за {price}₽</b>\n\n"
            f"Нажмите кнопку ниже, чтобы завершить оплату в YooKassa.",
            reply_markup=kb,
            parse_mode="HTML"
        )
        return await state.clear()

    new_order.status = "REJECTED"
    await session.commit()

    error_msg = str(payment_data.get("Message", "СБП сейчас недоступна"))
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="💳 Оплатить картой", callback_data="pay_method_official")],
        [types.InlineKeyboardButton(text="↩️ Перевод на карту (вручную)", callback_data="pay_method_sbp")],
    ])
    await callback.message.edit_text(
        "⚠️ <b>Онлайн-СБП пока недоступна.</b>\n\n"
        f"Причина: {error_msg}\n\n"
        "Выберите другой способ оплаты:",
        parse_mode="HTML",
        reply_markup=kb,
    )
    return await callback.answer("СБП недоступна, выберите другой способ", show_alert=True)


@router.callback_query(F.data == 'pay_method_official')
async def process_official_card_payment(
        callback: types.CallbackQuery,
        state: FSMContext,
        session: AsyncSession,
        club: Club,
        redis: Redis,
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
    sport_type = data.get('sport_type')  # 🌟 Извлекаем тип спорта
    payment_kind = data.get("payment_kind", "SUBSCRIPTION")
    idem_key = f"idem:bot:pay_official:{club.id}:{user_id}:{student_id}:{sport_type}:{lesson_count}:{days_to_add}:{payment_kind}"
    if not await rate_limit(redis, idem_key, 1, 90):
        await audit_block("bot_checkout_blocked", "duplicate_official_payment", club_id=club.id, user_id=user_id, student_id=student_id)
        return await callback.message.answer("Платеж уже создается. Подождите немного.")

    # 1. Проверяем настройки клуба
    user_res = await session.execute(select(User).where(User.user_id == user_id))
    user = user_res.scalar_one_or_none()

    if not user:
        return await callback.message.answer("❌ Ошибка: Пользователь не найден в системе.")

    # Совместимость со старыми пользователями: сначала используем их привязку,
    # а если она не заполнена — клуб текущего бота из middleware.
    club_id = getattr(club, "id", None)
    if not club_id:
        return await callback.message.answer("❌ Ошибка: Клуб не найден в вашей учетной записи.")
    if not club_id:
        return await callback.message.answer("❌ Ошибка: Не удалось определить ваш клуб.")

    pay_settings = (club.club_settings or {}).get("payments", {})
    shop_id = pay_settings.get("yookassa_shop_id")
    secret_key = pay_settings.get("yookassa_secret_key")

    if not shop_id or not secret_key:
        return await callback.message.answer("⚠️ Онлайн-оплата картой временно недоступна для этого клуба.")

    # 2. ПРОВЕРЯЕМ, ЕСТЬ ЛИ СОХРАНЕННАЯ КАРТА
    sub_query = select(Subscription).where(
        and_(
            Subscription.user_id == user_id,
            Subscription.club_id == club_id,
            Subscription.rebill_id.is_not(None)
        )
    )
    sub_res = await session.execute(sub_query)
    saved_subscription = sub_res.scalar_one_or_none()

    order_id = f"INIT_{uuid.uuid4().hex[:12].upper()}"
    yookassa_node = YooKassaClient(shop_id=shop_id, secret_key=secret_key, proxy_url=PROXY_URL)

    # =====================================================================
    # СЦЕНАРИЙ Б: КАРТА ЕСТЬ -> СПИСЫВАЕМ В 1 КЛИК
    # =====================================================================
    if saved_subscription and saved_subscription.rebill_id:
        new_order = PaymentOrder(
            id=order_id, user_id=user_id, student_id=student_id, club_id=club_id,
            amount_kopecks=amount_kopecks, lesson_count=lesson_count, days_to_add=days_to_add,
            status="NEW", type="FREEZE_RECURRING" if payment_kind == "FREEZE" else "RECURRING",
            discipline=sport_type  # 🌟 ЗАПИСЫВАЕМ СЕКЦИЮ В ОРДЕР ПЕРЕД КЛИКОМ
        )

        session.add(new_order)
        await session.commit()

        await callback.message.edit_text(
            "⏳ <b>Оплата в 1 клик...</b>\n\nСписываем средства со связанной карты. Пожалуйста, подождите.",
            parse_mode="HTML")

        ui_cfg = (club.club_settings or {}).get("ui", {})
        club_name = ui_cfg.get("club_name", club.name if club else "Фитнес-клуб")

        charge_data = await yookassa_node.charge_payment(
            order_id=order_id,
            amount_kopecks=amount_kopecks,
            payment_method_id=saved_subscription.rebill_id,
            club_name=club_name
        )

        if charge_data.get("Success"):
            if charge_data.get("Status") == "succeeded":
                new_order.status = "CONFIRMED"

                # 🌟 ФИКС: Если ЮKassa списала деньги моментально в фоне,
                # мы активируем абонемент прямо ЗДЕСЬ, передавая ПРАВИЛЬНУЮ дисциплину!
                if payment_kind == "FREEZE":
                    abon_result = await purchase_student_freeze(student_id, club_id, days_to_add, session)
                else:
                    abon_result = await add_abon(
                        student_id=student_id, lessons_count=lesson_count, session=session,
                        club_id=club_id, club_settings=club.club_settings,
                        days_to_add=days_to_add, discipline=sport_type)
                await session.commit()

                if abon_result:
                    new_expire, _ = abon_result
                    desc = (f"заморозка на {days_to_add} дн." if payment_kind == "FREEZE"
                            else ("БЕЗЛИМИТ" if lesson_count == 999 else f"{lesson_count} зан."))
                    human_disc = (club.club_settings or {}).get("disciplines", {}).get(str(sport_type).lower(), {}).get("name",
                                                                                                               sport_type)

                    await callback.message.edit_text(
                        f"⚡️ <b>Оплата успешно проведена в 1 клик!</b>\n\n"
                        f"🥋 Направление: <b>{human_disc}</b>\n"
                        f"💳 Списано: <b>{price}₽</b> с вашей сохраненной карты.\n"
                        f"📦 Абонемент (<b>{desc}</b>) успешно активирован и действует до: <b>{new_expire}</b>. 🔥"
                    )
                await state.clear()
                return
            else:
                # Если статус pending — управление уходит на вебхук.
                # (Чтобы вебхук не зачислил бокс, крайне рекомендую использовать Вариант №1 с колонкой в БД!)
                await state.clear()
                return
        else:
            new_order.status = "REJECTED"
            await session.commit()

            kb = types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="💳 Ввести данные карты вручную",
                                            callback_data="pay_method_official_force_new")]
            ])
            return await callback.message.edit_text(
                f"❌ <b>Не удалось списать оплату со связанной карты.</b>\n\n"
                f"Причина: {charge_data.get('Message', 'Отклонено банком')}.\n"
                f"Вы можете оплатить тариф вручную, введя данные заново:",
                parse_mode="HTML", reply_markup=kb
            )

    # =====================================================================
    # СЦЕНАРИЙ А: КАРТЫ НЕТ -> ГЕНЕРИРУЕМ ССЫЛКУ
    # =====================================================================
    new_order = PaymentOrder(
        id=order_id, user_id=user_id, student_id=student_id, club_id=club_id,
        amount_kopecks=amount_kopecks, lesson_count=lesson_count, days_to_add=days_to_add,
        status="NEW", type="FREEZE_FIRST" if payment_kind == "FREEZE" else "FIRST",
        discipline=sport_type  # 🌟 ЗАПИСЫВАЕМ СЕКЦИЮ В ОРДЕР ДЛЯ ВЕБХУКА
    )
    session.add(new_order)
    await session.commit()

    bot_info = await callback.bot.get_me()
    clean_bot_username = bot_info.username.replace("@", "")

    payment_data = await yookassa_node.init_payment(
        order_id=order_id,
        amount_kopecks=amount_kopecks,
        user_id=user_id,
        bot_username=clean_bot_username
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
        club,                 # Из нашей оптимизированной мидлвари
        club_settings: dict   # Из нашей оптимизированной мидлвари
):
    # 1. Сбор данных из стейта плательщика
    data = await state.get_data()
    student_id = data.get('student_id')
    sport_type = data.get('sport_type')         # Сюда прилетает: 'boxing', 'kickboxing', 'bjj', 'yoga'
    lesson_count = data.get('lesson_count', 0)
    days_to_add = data.get('days_to_add', 30)
    price = data.get('price', 0)
    payment_kind = data.get("payment_kind", "SUBSCRIPTION")

    photo_id = message.photo[-1].file_id
    user_id = message.from_user.id
    username = f"@{message.from_user.username}" if message.from_user.username else f"ID: {user_id}"

    # 2. Текст уведомления для владельца клуба
    discipline_name = club_settings.get("disciplines", {}).get(sport_type, {}).get("name", "Спорт")
    tariff_label = "<b>♾ БЕЗЛИМИТ</b>" if lesson_count == 999 else f"<b>🔢 {lesson_count} зан.</b>"

    admin_text = (
        f"📩 <b>НОВЫЙ ЧЕК — {club.name}</b>\n\n"
        f"👤 Отправитель: {username}\n"
        f"🥋 Секция: <b>{discipline_name}</b>\n"
        f"📊 Тариф: {tariff_label} на <b>{days_to_add} дн.</b> за <b>{price}₽</b>\n"
        f"🆔 ID Атлета: <code>{student_id}</code>\n"
    )

    # 🌟 КРИТИЧЕСКИЙ ФИКС: Сжимаем название секции в короткий шорткат для callback_data
    discipline_to_shortcut = {
        "boxing": "box",
        "kickboxing": "kick",
        "bjj": "bjj",
        "yoga": "yoga"
    }
    shortcut = "frz" if payment_kind == "FREEZE" else discipline_to_shortcut.get(str(sport_type).lower(), "box")

    # 3. Компактные кнопки для админа
    # Формат теперь строго: adm_confirm_[student_id]_[lesson_count]_[days_to_add]_[shortcut]
    admin_kb = InlineKeyboardBuilder()
    admin_kb.row(types.InlineKeyboardButton(
        text="✅ Подтвердить и активировать",
        callback_data=f"adm_confirm_{student_id}_{lesson_count}_{days_to_add}_{shortcut}") # <--- Зашили шорткат дисциплины!
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
        club,  # Из нашей оптимизированной мидлвари
        club_settings: dict  # Из нашей оптимизированной мидлвари
):
    # 1. ЗАЩИТА от двойного клика по кнопке
    if any(word in (callback.message.caption or "") for word in ["✅ ОФОРМЛЕНО", "✅ ОПЛАЧЕНО", "🟢 ОДОБРЕНО"]):
        return await callback.answer("Этот чек уже обработан!", show_alert=True)

    # 2. ПРОВЕРКА ПРАВ ДОСТУПА
    if callback.from_user.id != club.owner_id:
        return await callback.answer("❌ Вы не являетесь владельцем этого клуба!", show_alert=True)

    # 3. ПАРСИНГ данных из кнопки
    # Формат кнопки теперь: adm_confirm_[student_id]_[lesson_count]_[days_to_add]_[disc_shortcut]
    parts = callback.data.split('_')
    try:
        student_id = int(parts[2])
        count = int(parts[3])  # Получит число занятий или 999
        days_to_add = int(parts[4])  # Получит точные дни (30, 45, 90)

        # Защита: если дисциплина не передана в старой кнопке — страхуемся дефолтом
        disc_shortcut = parts[5] if len(parts) > 5 else "box"
    except (IndexError, ValueError):
        return await callback.answer("Ошибка распаковки данных кнопки ❌", show_alert=True)

    # Маппинг коротких ключей в реальные названия дисциплин из вашего DEFAULT_CLUB_SETTINGS
    shortcut_to_discipline = {
        "box": "boxing",
        "kick": "kickboxing",
        "bjj": "bjj",
        "yoga": "yoga"
    }
    is_paid_freeze = disc_shortcut == "frz"
    target_discipline = shortcut_to_discipline.get(disc_shortcut)
    if not target_discipline:
        active_disciplines = [
            code
            for code, info in club_settings.get("disciplines", {}).items()
            if isinstance(info, dict) and info.get("active")
        ]
        if active_disciplines:
            target_discipline = active_disciplines[0]
        else:
            target_discipline = next(iter(club_settings.get("disciplines", {}) or {}), "")

    if not target_discipline:
        return await callback.answer("Не удалось определить дисциплину клуба.", show_alert=True)

    # В callback хранится только компактный набор параметров, поэтому цену
    # восстанавливаем из настроек клуба, а не обращаемся к несуществующим
    # переменным старого обработчика.
    discipline_cfg = club_settings.get("disciplines", {}).get(target_discipline, {}) or {}
    selected_tariff = next(
        (tariff for tariff in discipline_cfg.get("tariffs", [])
         if int(tariff.get("count", 0) or 0) == count
         and int(tariff.get("days", 30) or 30) == days_to_add),
        {},
    )
    amount_kopecks = int(round(float(selected_tariff.get("price", 0) or 0) * 100))

    # 4. ЛОГИКА ЗАЧИСЛЕНИЯ абонемента в СУБД (Передаем дисциплину!)
    # Чтобы логика add_abon не ломалась, мы передаем target_discipline внутрь.
    # Убедитесь, что ваша функция add_abon принимает аргумент discipline!
    result = (await purchase_student_freeze(student_id, club.id, days_to_add, session)
              if is_paid_freeze else await add_abon(
                  student_id=student_id, lessons_count=count, session=session,
                  club_id=club.id, club_settings=club_settings,
                  days_to_add=days_to_add, discipline=target_discipline))

    if result:
        new_expire, parent_id = result
        session.add(PaymentOrder(
            id=f"CASH_SUB_{uuid.uuid4().hex[:16].upper()}",
            user_id=int(parent_id) if parent_id else None,
            student_id=student_id,
            club_id=club.id,
            amount_kopecks=amount_kopecks,
            lesson_count=count,
            days_to_add=days_to_add,
            status="CONFIRMED",
            type="CASH",
            discipline=target_discipline,
            provider_payment_id=f"CASH_SUB_{uuid.uuid4().hex[:16].upper()}",
        ))
        await session.commit()

        # Красивый статус для админского экрана
        desc = f"заморозка на {days_to_add} дн." if is_paid_freeze else ("БЕЗЛИМИТ" if count == 999 else f"{count} зан.")
        human_disc = club_settings.get("disciplines", {}).get(target_discipline, {}).get("name", target_discipline)

        # 5. UI: Полностью убираем инлайн-кнопки под фоткой чека и пишем статус
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.edit_caption(
            caption=callback.message.caption + f"\n\n🟢 <b>ОДОБРЕНО АДМИНОМ!</b>\n🥋 Направление: {human_disc}\n📦 Тариф: {desc}\n📅 Продлен до: {new_expire}",
            parse_mode="HTML"
        )

        # 6. SaaS-УВЕДОМЛЕНИЕ ДЛЯ КЛИЕНТА (РОДИТЕЛЯ)
        try:
            ui_club_name = club_settings.get("ui", {}).get("club_name")
            club_name = club.name if not ui_club_name or ui_club_name == "Новый фитнес-клуб" else ui_club_name

            await callback.bot.send_message(
                chat_id=parent_id,
                text=f"🥳 <b>Отличные новости!</b>\n\n"
                     f"Ваша оплата в фитнес-клуб <b>{club_name}</b> успешно проверена.\n"
                     f"Абонемент <b>{human_disc}</b> (<b>{desc}</b>) успешно активирован и действует до: <b>{new_expire}</b>. 🔥\n\n"
                     f"<i>Ждем вас на тренировках!</i>",
                parse_mode="HTML"
            )
        except Exception as e:
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

    builder.row(InlineKeyboardButton(text="⬅️ В админ-панель", callback_data="admin"))

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
        days_to_add=days,
        discipline=sport_type
    )

    if result:
        new_expire, parent_id = result
        student = await session.get(Student, student_id)

        # Формируем красивое отображение тарифа для экрана админа (прячем техническое 999)
        cash_amount = int(float(selected_tariff.get("price", 0) or 0) * 100)
        session.add(PaymentOrder(id=f"CASH_ABON_{uuid.uuid4().hex[:24]}", user_id=parent_id,
                                 student_id=student_id, club_id=club.id, discipline=sport_type,
                                 amount_kopecks=cash_amount, status="CONFIRMED",
                                 type="CASH_SUBSCRIPTION", provider_payment_id=f"CASH:{uuid.uuid4().hex}",
                                 lesson_count=count, days_to_add=days))
        await session.commit()
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
                parent_label = await resolve_user_label(session, parent_id, empty_label="Плательщик")
                student_label = getattr(student, "name", None) or f"ID {student_id}"
                cash_receipt = build_owner_receipt_text(
                    title="Оплата наличными подтверждена",
                    order_id=f"CASH_ABON_{student_id}_{tariff_idx}",
                    buyer_label=parent_label,
                    items_text=format_order_items([
                        type("ItemView", (), {"title": f"{disc_cfg.get('name')} · {t_label}", "quantity": 1, "product_id": 1})()
                    ]),
                    amount_kopecks=int(float(selected_tariff.get("price", 0) or 0) * 100),
                    extra_lines=[
                        f"Атлет: <b>{student_label}</b>",
                        f"Клуб: <b>{club_name}</b>",
                        f"Абонемент до: <b>{new_expire}</b>",
                        "График занятий смотрите во вкладке <b>Расписание</b>.",
                    ],
                )
                await callback.bot.send_message(
                    chat_id=parent_id,
                    text=cash_receipt,
                    parse_mode="HTML"
                )
                if club.owner_id and int(club.owner_id) != int(parent_id):
                    await callback.bot.send_message(chat_id=int(club.owner_id), text=cash_receipt, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления родителю {parent_id}: {e}")

        # Полностью очищаем стейт админа после успешного платежа
        await state.clear()
    else:
        await callback.answer("Ошибка при обновлении данных студента в БД ❌", show_alert=True)


@router.callback_query(F.data == 'manage_subscription')
async def process_manage_subscription(callback: types.CallbackQuery, session: AsyncSession):
    """Главный экран управления картами: проверка привязанной карты"""
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
    audit_event(
        "bot_card_management_opened",
        club_id=subscriptions[0].club_id if subscriptions else None,
        actor_user_id=user_id,
        actor_role="client",
        actor_name=callback.from_user.full_name,
        action="open",
        object_type="subscription_card",
        object_id=user_id,
        location="bot/manage_subscription",
        has_saved_card=bool(subscriptions),
    )

    if not subscriptions:
        # Если привязанных карт в базе нет
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔙 Назад в профиль", callback_data="profile")] # Замени коллбэк назад на свой, если нужно
        ])
        return await callback.message.edit_text(
            "💳 <b>Управление картами</b>\n\n"
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
        f"💳 <b>УПРАВЛЕНИЕ КАРТАМИ</b>\n\n"
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
    audit_event(
        "bot_card_delete_confirm_opened",
        actor_user_id=callback.from_user.id,
        actor_role="client",
        actor_name=callback.from_user.full_name,
        action="open",
        object_type="subscription_card",
        object_id=callback.from_user.id,
        location="bot/manage_subscription/confirm_delete",
    )

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
        
        await session.commit()
        logger.info(f"🗑️ Пользователь {user_id} полностью удалил свои банковские карты из СУБД.")
        audit_event(
            "bot_card_deleted",
            club_id=subscriptions[0].club_id,
            actor_user_id=user_id,
            actor_role="client",
            actor_name=callback.from_user.full_name,
            action="delete",
            object_type="subscription_card",
            object_id=user_id,
            location="bot/manage_subscription/delete_card",
            deleted_cards=len(subscriptions),
        )

    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🔙 Вернуться в профиль", callback_data="profile")]
    ])

    await callback.message.edit_text(
        "✅ <b>Карта успешно удалена!</b>\n\n"
        "Ваши платежные данные полностью стерты из системы клуба.\n"
        "Автопродление отключено.",
        parse_mode="HTML",
        reply_markup=kb)

