from datetime import datetime, timedelta

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


@router.callback_query(F.data.startswith('set_limit_'))
async def process_kids_limit(
        callback: types.CallbackQuery,
        state: FSMContext,
        club_settings: dict
):
    # 1. Извлекаем лимит из callback_data (set_limit_8 -> 8 или set_limit_boxing_8 -> 8)
    parts = callback.data.split('_')
    try:
        # Берем последний элемент, так надежнее всего
        limit = int(parts[-1])
    except (IndexError, ValueError):
        return await callback.answer("Ошибка данных кнопки", show_alert=True)

    # 2. Берем данные из FSM
    data = await state.get_data()
    sport_type = data.get('sport_type')

    # Если вдруг стейт сбросился, пытаемся взять код дисциплины из середины колбэка
    if not sport_type and len(parts) > 2:
        sport_type = parts[2]
        await state.update_data(sport_type=sport_type)

    if not sport_type:
        return await callback.answer("Сессия истекла, начните выбор заново", show_alert=True)

    # 3. Достаем конфиг конкретной секции
    discipline_cfg = club_settings.get("disciplines", {}).get(sport_type, {})
    if not discipline_cfg:
        return await callback.answer("Ошибка: секция не найдена", show_alert=True)

    # 4. ЛОГИКА ОПРЕДЕЛЕНИЯ ЦЕНЫ
    price = None
    display_name = discipline_cfg.get('name', 'Секция')

    if discipline_cfg.get("type") == "unlimited":
        price = discipline_cfg.get("price")
        label = "Безлимит"
    else:
        tariffs = discipline_cfg.get("tariffs", [])
        # Ищем тариф, где count совпадает с выбранным лимитом
        selected_tariff = next((t for t in tariffs if int(t['count']) == limit), None)
        if selected_tariff:
            price = selected_tariff.get('price')
        label = f"{limit} зан."

    if price is None:
        logger.error(f"Цена не найдена для {sport_type} (limit: {limit})")
        return await callback.answer("Ошибка: тариф не найден в базе клуба", show_alert=True)

    # 5. ФИКС NONE: Сохраняем все данные, включая ID атлета
    # Если student_id не был выбран ранее (в цепочке "за себя/ребенка"),
    # используем ID того, кто нажал на кнопку.
    current_student_id = data.get('student_id') or callback.from_user.id

    await state.update_data(
        student_id=current_student_id,
        lesson_count=limit,
        price=price,
        discipline_name=display_name
    )
    await state.set_state(PaymentStates.waiting_for_receipt)

    # 6. UI часть (Реквизиты)
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
        f"<i>После проверки админом занятия будут зачислены автоматически.</i>"
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
        club: Club,  # Объект из БД (через мидлварь)
        club_settings: dict  # Настройки из Redis/БД
):
    # 1. Сбор данных (Исправил ключи на те, что были в прошлых хендлерах)
    data = await state.get_data()
    # ВАЖНО: Проверь, какой ключ ты использовал выше!
    student_id = data.get('student_id') or data.get('chosen_student_id')
    sport_type = data.get('sport_type')
    lesson_count = data.get('lesson_count', 0)
    price = data.get('price', 0)

    photo_id = message.photo[-1].file_id
    user_id = message.from_user.id
    username = f"@{message.from_user.username}" if message.from_user.username else f"ID: {user_id}"

    # 2. Формируем текст уведомления
    discipline_name = club_settings.get("disciplines", {}).get(sport_type, {}).get("name", "Спорт")

    # Красивое отображение тарифа
    tariff_label = f"<b>{lesson_count} зан.</b>" if lesson_count > 0 else "<b>БЕЗЛИМИТ</b>"

    admin_text = (
        f"📩 <b>НОВЫЙ ЧЕК — {club.name}</b>\n\n"
        f"👤 Отправитель: {username}\n"
        f"🥋 Секция: <b>{discipline_name}</b>\n"
        f"📊 Тариф: {tariff_label} за {price}₽\n"
        f"🆔 ID Атлета: <code>{student_id}</code>\n"
    )

    # 3. Кнопки для админа
    admin_kb = InlineKeyboardBuilder()
    # Передаем всё важное в колбэк (но помни про лимит 64 байта в callback_data!)
    admin_kb.row(types.InlineKeyboardButton(
        text="✅ Подтвердить",
        callback_data=f"adm_confirm_{student_id}_{lesson_count}_{sport_type}")
    )
    admin_kb.row(types.InlineKeyboardButton(
        text="❌ Отклонить",
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

    await state.clear()


@router.callback_query(F.data.startswith('adm_confirm_'))
async def admin_confirm_payment(
    callback: types.CallbackQuery,
    session: AsyncSession,
    club: Club,
    club_settings: dict
):
    # 1. ЗАЩИТА: проверяем текст, чтобы не нажать дважды
    if "✅ ОФОРМЛЕНО" in (callback.message.caption or "") or "✅ ОПЛАЧЕНО" in (callback.message.caption or ""):
        return await callback.answer("Этот чек уже обработан!", show_alert=True)

    # 2. ПРОВЕРКА ПРАВ: только админ этого клуба может тыкать
    if callback.from_user.id != club.owner_id:
        return await callback.answer("❌ Это не ваш клуб!", show_alert=True)

    # 3. ПАРСИНГ
    parts = callback.data.split('_')
    try:
        student_id = int(parts[2])
        count = int(parts[3])
    except (IndexError, ValueError):
        return await callback.answer("Ошибка в данных кнопки", show_alert=True)

    # 4. ЛОГИКА ЗАЧИСЛЕНИЯ
    result = await add_abon(
        student_id=student_id,
        lessons_count=count,
        session=session,
        club_id=club.id,
        club_settings=club_settings
    )

    if result:
        new_expire, parent_id = result
        desc = f"{count} зан." if count > 0 else "БЕЗЛИМИТ"

        # 5. UI: Убираем кнопки и пишем статус
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.edit_caption(
            caption=callback.message.caption + f"\n\n✅ <b>ОФОРМЛЕНО: {desc}</b>\n📅 До: {new_expire}",
            parse_mode="HTML"
        )

        # 6. УВЕДОМЛЕНИЕ РОДИТЕЛЯ
        try:
            await callback.bot.send_message(
                chat_id=parent_id,
                text=f"🥳 Оплата в <b>{club.name}</b> подтверждена! До <b>{new_expire}</b>",
                parse_mode="HTML"
            )
        except TelegramForbiddenError:
            logger.info(f"Юзер {parent_id} заблокировал бота. Уведомление не доставлено.")
        except TelegramRetryAfter as e:
            logger.warning(f"Флуд-контроль! Нужно подождать {e.retry_after} сек.")
        except TelegramAPIError as e:
            logger.error(f"Ошибка API при уведомлении {parent_id}: {e}")
        except Exception as e:
            logger.error(f"Непредвиденная ошибка: {e}")
        await callback.answer("Успешно зачислено! ✅")
    else:
        await callback.answer("❌ Ошибка: Атлет не найден", show_alert=True)


@router.callback_query(F.data.startswith("adm_decline_"))
async def admin_decline_pay(
    callback: types.CallbackQuery,
    club: Club  # <--- Достаем клуб из мидлвари
):
    # 1. Достаем ID родителя из callback_data
    parent_id = int(callback.data.split("_")[-1])

    # 2. Обновляем сообщение у АДМИНА
    # Добавляем пометку прямо в подпись к фото
    await callback.message.edit_caption(
        caption=callback.message.caption + "\n\n❌ <b>ОТКЛОНЕНО АДМИНИСТРАТОРОМ</b>",
        parse_mode="HTML"
    )

    # 3. Уведомляем РОДИТЕЛЯ
    try:
        await callback.bot.send_message(
            chat_id=parent_id,
            text=(
                f"❌ <b>Ваш чек отклонен</b>\n"
                f"📍 Клуб: <b>{club.name}</b>\n\n"
                f"Пожалуйста, проверьте правильность данных или свяжитесь с администратором клуба: "
                f"{club.club_settings.get('ui', {}).get('support_link', 'в профиле')}"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        # Если юзер заблокировал бота, просто логируем
        print(f"Не удалось отправить отказ юзеру {parent_id}: {e}")

    await callback.answer("Оплата отклонена")


@router.callback_query(F.data == "admin_cash_list")
async def show_all_students_for_cash(
        callback: types.CallbackQuery,
        session: AsyncSession,
        club: Club  # <--- Наша мидлварь принесла объект клуба
):
    # 1. Фильтруем студентов ТОЛЬКО этого клуба (изоляция данных)
    stmt = select(Student).where(
        Student.club_id == club.id
    ).order_by(Student.name)

    result = await session.execute(stmt)
    students = result.scalars().all()

    if not students:
        return await callback.answer(f"В клубе {club.name} пока нет атлетов", show_alert=True)

    # 2. Собираем клавиатуру
    builder = InlineKeyboardBuilder()
    for s in students:
        # Показываем имя и баланс для удобства админа
        builder.row(InlineKeyboardButton(
            text=f"👤 {s.name} (баланс: {s.balance_lessons})",
            callback_data=f"cash_pay_{s.id}")
        )

    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_keyboard"))

    await callback.message.edit_text(
        f"📍 Клуб: <b>{club.name}</b>\n"
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

    # Получаем направление, которое админ выбрал ранее (если выбрал)
    data = await state.get_data()
    sport_type = data.get('sport_type', 'default')

    # 1. Проверяем тип дисциплины в конфиге этого клуба
    # Ищем: "disciplines" -> "bjj" -> "type" (unlimited или lessons)
    disc_cfg = club_settings.get("disciplines", {}).get(sport_type, {})
    is_unlimited = disc_cfg.get("type") == "unlimited"

    # Сохраняем ID студента для следующего шага (если это уроки)
    await state.update_data(cash_student_id=student_id)

    if is_unlimited:
        # 2. Если безлимит — сразу оформляем (lessons_count=0)
        result = await add_abon(student_id, lessons_count=0, session=session, club_id=club.id,
                                club_settings=club_settings)

        if result:
            new_expire, parent_id = result
            await callback.message.edit_text(
                f"✅ <b>Наличные приняты (Безлимит)</b>\n"
                f"Направление: <b>{disc_cfg.get('name', sport_type.upper())}</b>\n"
                f"Продлено до: <b>{new_expire}</b>",
                parse_mode="HTML"
            )

            # Уведомляем родителя (SaaS-стиль: пишем название клуба)
            if parent_id:
                try:
                    await callback.bot.send_message(
                        chat_id=parent_id,
                        text=f"💵 <b>Оплата наличными подтверждена!</b>\n"
                             f"Ваш абонемент продлен до: <b>{new_expire}</b>.",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    print(f"Ошибка уведомления: {e}")

            await state.clear()
        else:
            await callback.answer("Ошибка: студент не найден", show_alert=True)

    else:
        # 3. Если это уроки — показываем выбор (8, 12 зан. и т.д.)
        # Передаем настройки тарифов в клавиатуру
        await callback.message.edit_text(
            f"💰 <b>Наличные: {disc_cfg.get('name')}</b>\nВыберите пакет:",
            reply_markup=get_cash_options_kb(disc_cfg),  # Новая функция ниже
            parse_mode="HTML"
        )
        await callback.answer()


@router.callback_query(F.data.startswith("confirm_cash_"))
async def final_cash_pay(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, club: Club,
                         club_settings: dict):
    data = await state.get_data()
    student_id = data.get('cash_student_id')
    count = int(callback.data.split("_")[-1])

    result = await add_abon(student_id, lessons_count=count, session=session, club_id=club.id,
                            club_settings=club_settings)

    if result:
        new_expire, _ = result
        await callback.message.edit_text(f"✅ <b>Успешно!</b>\nАбонемент продлен до {new_expire}")
        await state.clear()


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


# 2. Обязательный ответ на pre_checkout_query (ТГ ждет его 10 сек)
@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)


# 3. Финальный этап: зачисление подписки после успешной оплаты
@router.message(F.successful_payment)
async def on_successful_payment(message: Message, session: AsyncSession, club: Club,  redis: Redis):
    payload = message.successful_payment.invoice_payload
    days = int(payload.split("_")[-1])

    now = datetime.now()

    # Если подписка уже есть и не истекла — плюсуем к ней
    if club.subscription_expire_at and club.subscription_expire_at > now:
        club.subscription_expire_at += timedelta(days=days)
    else:
        # Если истекла или новая — считаем от текущего момента
        club.subscription_expire_at = now + timedelta(days=days)

    await session.commit()
    cache_key = f"club_config:{message.bot.token}"
    await redis.delete(cache_key)
    logger.info(f"Кэш сброшен для бота: {message.bot.token}")

    await message.answer(
        f"✅ Оплата прошла успешно!\n"
        f"Подписка продлена на <b>{days} дней</b>.\n"
        f"Новая дата окончания: <b>{club.subscription_expire_at.strftime('%d.%m.%Y')}</b>",
        parse_mode="HTML")
