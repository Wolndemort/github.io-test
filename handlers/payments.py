from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import Student, Club
from handlers.buttons import discipline, get_pay_options_kb
from database.db import add_abon
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton
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


@router.callback_query(F.data.startswith('student_pay_'))
async def select_tariff_handler(
    callback: types.CallbackQuery,
    state: FSMContext,
    club_settings: dict  # <--- Опять берем из мидлвари
):
    # 1. Достаем ID студента и данные из FSM
    student_id = int(callback.data.split('_')[2])
    data = await state.get_data()
    sport_type = data.get("sport_type")

    # Сохраняем студента в стейт для финальной транзакции
    await state.update_data(student_id=student_id)

    # 2. Ищем настройки именно этого спорта в конфиге клуба
    discipline_cfg = club_settings.get("disciplines", {}).get(sport_type)

    if not discipline_cfg or not discipline_cfg.get("active"):
        return await callback.answer("Ошибка: тарифы для этой секции не найдены", show_alert=True)

    # 3. Генерируем клавиатуру оплаты (используем твою функцию, что писали выше)
    # Она сама поймет: unlimited или lessons
    markup = get_pay_options_kb(discipline_cfg)

    # 4. Выводим инфу
    await callback.message.edit_text(
        f"👤 Атлет: <b>ID {student_id}</b>\n"
        f"🥋 Секция: <b>{discipline_cfg['name']}</b>\n\n"
        "<b>Выберите подходящий тариф:</b>",
        reply_markup=markup,
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith('set_limit_'))
async def process_kids_limit(
        callback: types.CallbackQuery,
        state: FSMContext,
        club_settings: dict
):
    # Достаем выбранный лимит (например, 8, 12)
    limit = int(callback.data.split('_')[2])
    data = await state.get_data()
    sport_type = data.get('sport_type')

    # Ищем цену этого лимита в конфиге
    discipline_cfg = club_settings.get("disciplines", {}).get(sport_type, {})
    tariffs = discipline_cfg.get("tariffs", [])

    # Находим нужный тариф в списке
    selected_tariff = next((t for t in tariffs if t['count'] == limit), None)
    price = selected_tariff['price'] if selected_tariff else "Ошибка"

    await state.update_data(lesson_count=limit, price=price)
    await state.set_state(PaymentStates.waiting_for_receipt)

    ui = club_settings.get("ui", {})
    payment_info = ui.get("payment_info", "Реквизиты не указаны")

    await callback.message.edit_text(
        f"💰 <b>Оплата: {discipline_cfg.get('name')}</b>\n"
        f"Тариф: <b>{limit} зан. — {price}₽</b>\n\n"
        f"💳 Реквизиты: <code>{payment_info}</code>\n\n"
        "Пришлите скриншот чека в ответ на это сообщение:",
        parse_mode="HTML"
    )


@router.message(PaymentStates.waiting_for_receipt, F.photo)
async def handle_receipt_submission(
        message: types.Message,
        state: FSMContext,
        session: AsyncSession,
        club: Club,  # Из мидлвари
        club_settings: dict  # Из мидлвари
):
    # 1. Собираем всё, что накэшировали в FSM
    data = await state.get_data()
    student_id = data.get('chosen_student_id')
    sport_type = data.get('sport_type')
    lesson_count = data.get('lesson_count')

    photo_id = message.photo[-1].file_id  # Берем лучшее качество
    user_id = message.from_user.id
    username = f"@{message.from_user.username}" if message.from_user.username else f"ID: {user_id}"

    # 2. Формируем красивое уведомление для админа этого клуба
    discipline_name = club_settings.get("disciplines", {}).get(sport_type, {}).get("name", sport_type.upper())

    admin_text = (
        f"📩 <b>НОВЫЙ ЧЕК НА ПРОВЕРКУ</b>\n\n"
        f"🏛 Клуб: <b>{club.name}</b>\n"
        f"👤 Отправитель: {username}\n"
        f"🥋 Секция: <b>{discipline_name}</b>\n"
        f"📊 Тариф: <b>{lesson_count if lesson_count > 0 else 'Безлимит'} зан.</b>\n"
        f"🆔 ID Атлета: <code>{student_id}</code>\n"
    )

    # Клавиатура для админа, чтобы подтвердить в один клик
    # В callback_data зашиваем ID студента и количество занятий
    admin_kb = InlineKeyboardBuilder()
    admin_kb.row(types.InlineKeyboardButton(
        text="✅ Подтвердить и зачислить",
        callback_data=f"confirm_pay_{student_id}_{lesson_count}")
    )
    admin_kb.row(types.InlineKeyboardButton(
        text="❌ Отклонить (Левый чек)",
        callback_data=f"reject_pay_{user_id}")
    )

    # 3. Отправляем админу (используем owner_id из объекта Club)
    try:
        await message.bot.send_photo(
            chat_id=club.owner_id,
            photo=photo_id,
            caption=admin_text,
            reply_markup=admin_kb.as_markup(),
            parse_mode="HTML"
        )
    except Exception as e:
        # Если админ не запустил бота, можно продублировать в группу логирования
        print(f"Ошибка отправки админу {club.owner_id}: {e}")

    # 4. Ответ пользователю
    support = club_settings.get("ui", {}).get("support_link", "@admin")
    await message.answer(
        f"✅ <b>Чек отправлен администратору {club.name}!</b>\n\n"
        "Мы проверим его в ближайшее время. После подтверждения "
        "абонемент обновится автоматически.\n\n"
        f"Связь с админом: {support}",
        parse_mode="HTML"
    )

    await state.clear()


@router.callback_query(F.data.startswith('adm_confirm_'))
async def admin_confirm_payment(
        callback: types.CallbackQuery,
        session: AsyncSession,
        club: Club,
        club_settings: dict  # Опять же, мидлварь знает, какой админ нажал
):
    # 1. Парсим данные из callback_data (student_id и lessons_count)
    # adm_confirm_123_8 -> ['adm', 'confirm', '123', '8']
    parts = callback.data.split('_')
    student_id = int(parts[2])
    count = int(parts[3])

    # 2. Вызываем твою функцию зачисления (она у тебя уже есть)
    # Тут можно добавить проверку, чтобы только owner_id этого клуба мог подтвердить
    result = await add_abon(
        student_id=student_id,
        lessons_count=count,
        session=session,
        club_id=club.id,
        club_settings=club_settings
    )

    if result:
        new_expire, parent_id = result  # add_abon должен возвращать parent_id для уведомления

        # 3. Уведомляем админа
        await callback.message.edit_caption(
            caption=callback.message.caption + f"\n\n✅ <b>ОПЛАЧЕНО. Продлен до {new_expire}</b>",
            parse_mode="HTML"
        )

        # 4. Уведомляем РОДИТЕЛЯ (клиента)
        try:
            await callback.bot.send_message(
                chat_id=parent_id,
                text=f"🥳 <b>Оплата подтверждена!</b>\n\n"
                     f"Абонемент для атлета (ID: {student_id}) успешно продлен до <b>{new_expire}</b>.\n"
                     f"Ждем вас на тренировке в <b>{club.name}</b>!",
                parse_mode="HTML"
            )
        except Exception:
            pass  # Если юзер заблокировал бота

    await callback.answer("Абонемент успешно активирован!")


@router.callback_query(F.data.startswith("adm_confirm_"))
async def admin_confirm_pay(callback: types.CallbackQuery, session: AsyncSession, club: Club, club_settings: dict):
    # ПРОВЕРКА: Если в тексте уже есть "✅ ОФОРМЛЕНО", значит кнопка нажата ранее
    if "✅ ОФОРМЛЕНО" in (callback.message.caption or ""):
        return await callback.answer("Этот чек уже обработан!", show_alert=True)

    parts = callback.data.split('_')
    student_id = int(parts[2])
    count = int(parts[3])

    result = await add_abon(student_id, lessons_count=count, session=session, club_id=club.id,
                            club_settings=club_settings)
    if result:
        new_expire, parent_id = result
        desc = f"{count} зан." if count > 0 else "БЕЗЛИМИТ"

        # 3. МЕНЯЕМ КЛАВИАТУРУ У АДМИНА (Убираем кнопки "Оформить/Отклонить")
        # Это исключает повторное нажатие
        await callback.message.edit_reply_markup(reply_markup=None)

        # 4. Обновляем текст
        await callback.message.edit_caption(
            caption=callback.message.caption + f"\n\n✅ <b>ОФОРМЛЕНО: {desc}</b>\n📅 До: {new_expire}",
            parse_mode="HTML"
        )

        # ... (логика уведомления родителя остается как у тебя)


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
            f"Выберите пакет (Наличные) для <b>{disc_cfg.get('name', sport_type)}</b>:",
            reply_markup=get_pay_options_kb(disc_cfg).as_markup(),  # Передай конфиг в кнопки!
            parse_mode="HTML"
        )
        await callback.answer()
