from datetime import datetime, timedelta
from sqlalchemy import update
from aiogram.utils.keyboard import InlineKeyboardBuilder
from redis import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramAPIError

from database.db import Student, Club
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
    discipline_code = callback.data.split('_')[1]
    await state.update_data(sport_type=discipline_code)

    # Ищем детей юзера в БД (используем твои модели)
    from database.db import Student
    res = await session.execute(select(Student).where(Student.parent_id == callback.from_user.id))
    students = res.scalars().all()

    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="🙋‍♂️ За себя", callback_data=f"set_at_me"))
    for s in students:
        kb.row(types.InlineKeyboardButton(text=f"👦 {s.name}", callback_data=f"set_at_{s.id}"))

    await callback.message.edit_text("<b>Для кого оформляем абонемент?</b>", reply_markup=kb.as_markup(),
                                     parse_mode="HTML")


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
@router.callback_query(F.data.startswith('set_tariff_'))
async def process_kids_limit(
        callback: types.CallbackQuery,
        state: FSMContext,
        club_settings: dict
):
    # 1. Извлекаем sport_type и индекс тарифа из нового callback_data
    # Формат: set_tariff_[sport_type]_[tariff_idx] -> set_tariff_kickboxing_0
    parts = callback.data.split('_')
    try:
        sport_type = parts[2]
        tariff_idx = int(parts[3])
    except (IndexError, ValueError):
        return await callback.answer("Ошибка обработки кнопки тарифа ❌", show_alert=True)

    # 2. Подстраховка для FSM (если вдруг сбросился sport_type)
    await state.update_data(sport_type=sport_type)

    # 3. Достаем конфиг конкретной секции
    discipline_cfg = club_settings.get("disciplines", {}).get(sport_type, {})
    if not discipline_cfg:
        return await callback.answer("Ошибка: секция не найдена 🛠", show_alert=True)

    # 4. Достаем список тарифов и берем нужный строго по ИНДЕКСУ
    tariffs = discipline_cfg.get("tariffs", [])
    if tariff_idx >= len(tariffs):
        return await callback.answer("Ошибка: выбранный тариф больше не существует ❌", show_alert=True)

    selected_tariff = tariffs[tariff_idx]

    # Извлекаем параметры из унифицированного тарифа
    price = selected_tariff.get('price')
    days = selected_tariff.get('days', 30)
    count = selected_tariff.get('count', 0)  # Будет 999 для безлимита или число (8, 12) для уроков

    d_type = discipline_cfg.get("type", "lessons")
    display_name = discipline_cfg.get('name', 'Секция')

    # Формируем красивый текст тарифа для вывода юзеру на экран
    if d_type == "unlimited" or count == 999:
        label = f"Безлимит на {days} дней"
    else:
        label = f"{count} зан. / {days} дн."

    # 5. Сохраняем ВСЕ данные в FSM, включая точные дни действия тарифа
    data = await state.get_data()
    current_student_id = data.get('student_id') or callback.from_user.id

    await state.update_data(
        student_id=current_student_id,
        lesson_count=count,  # Сюда запишется либо 8/12, либо 999
        days_to_add=days,  # <--- ВАЖНО: сохраняем дни, чтобы админ-хендлер их видел!
        price=price,
        discipline_name=display_name
    )

    # Переводим пользователя в состояние ожидания скриншота чека
    await state.set_state(PaymentStates.waiting_for_receipt)

    # 6. UI часть (Вывод реквизитов СБП из конфига клуба)
    ui_cfg = club_settings.get("ui", {})
    payment_info = ui_cfg.get("payment_info")

    if not payment_info or "+79000000000" in payment_info:
        payment_info = "⚠️ Реквизиты временно не указаны. Пожалуйста, свяжитесь с администратором."

    text = (
        f"💰 <b>Оплата: {display_name}</b>\n"
        f"Тариф: <b>{label} — {price}₽</b>\n\n"
        f"💳 <b>Реквизиты для перевода (СБП):</b>\n"
        f"<code>{payment_info}</code>\n\n"
        f"<b>Шаг 1:</b> Переведите {price}₽ по указанным реквизитам.\n"
        f"<b>Шаг 2:</b> Пришлите <b>скриншот чека</b> сюда в ответ на это сообщение.\n\n"
        f"<i>После проверки админом абонемент активируется автоматически.</i>"
    )

    await callback.message.edit_text(
        text=text,
        parse_mode="HTML"
    )
    await callback.answer()


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
            club_name = club_settings.get("ui", {}).get("club_name", club.name)
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
    student_id = int(callback.data.split("_")[-1])

    # Получаем направление, которое админ выбрал на предыдущем шаге
    data = await state.get_data()
    sport_type = data.get('sport_type', 'default')

    # Ищем конфигурацию этой дисциплины в настройках клуба
    disc_cfg = club_settings.get("disciplines", {}).get(sport_type, {})

    if not disc_cfg:
        return await callback.answer("Ошибка: Данное направление еще не настроено в клубе 🛠", show_alert=True)

    # ВАЖНО: Сохраняем и ID студента, и код дисциплины (sport_type) для хендлера подтверждения
    await state.update_data(
        cash_student_id=student_id,
        cash_sport_type=sport_type  # Без этого хендлер подтверждения не найдет тариф!
    )

    # Показываем клавиатуру настроенных тарифов для ЛЮБОГО типа секции
    await callback.message.edit_text(
        f"💰 <b>Прием наличных: {disc_cfg.get('name')}</b>\n"
        f"Выберите тарифный план, который оплатил атлет:",
        reply_markup=get_cash_options_kb(disc_cfg),  # Выведет и безлимиты, и уроки по индексам
        parse_mode="HTML"
    )
    await callback.answer()


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

