from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import AsyncSessionLocal, get_student_list, Student
from config import ADMIN_IDS
from handlers.buttons import discipline, kids_pay_options
from database.db import add_abon
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton
from aiogram import Router, F, types
from loguru import logger
from handlers.states import PaymentStates


router = Router()


@router.callback_query(F.data == 'choose_section')
async def show_sections(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "<b>Выберите направление тренировок:</b>",
        reply_markup=discipline(),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith('buy_'))
async def buy_handler(callback: types.CallbackQuery, state: FSMContext, session: AsyncSessionLocal):
    user = callback.from_user
    sport_type = callback.data.split('_')[1]
    await state.update_data(sport_type=sport_type)
    students = await get_student_list(user.id, session)
    if not students:
        await callback.answer("Сначала добавьте атлета в профиле!", show_alert=True)
        return
    await state.update_data(sport_type=sport_type)
    builder = InlineKeyboardBuilder()
    for s in students:
        builder.row(InlineKeyboardButton(
            text=f"🥋 {s.name}",
            callback_data=f"student_pay_{s.id}")
        )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="begin"))
    await callback.message.edit_text(
        f"Вы выбрали направление: <b>{sport_type.upper()}\n</b>"
        "Теперь выберите, за кого вносится оплата:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith('student_pay_'))
async def request_receipt(callback: types.CallbackQuery, state: FSMContext):
    student_id = int(callback.data.split('_')[2])
    await state.update_data(chosen_student_id=student_id)
    data = await state.get_data()
    sport_type = data.get('sport_type')
    if sport_type == 'bjj':
        await state.update_data(lesson_count=0)
        await state.set_state(PaymentStates.waiting_for_receipt)
        await callback.message.edit_text(
            "💳 <b>Реквизиты (Взрослый):</b>\n\nСумма: 5000₽ (Безлимит)\n"
            "СПБ: `+79606666165` (Адам.О)\n\n"
            "Пришлите скриншот чека:",
            parse_mode="HTML"
        )
    else:
        await callback.message.edit_text(
            "Выберите тип абонемента для ребенка:",
            reply_markup=kids_pay_options().as_markup()
        )
    await callback.answer()


@router.callback_query(F.data.startswith('set_limit_'))
async def process_pay_choice(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    count = int(callback.data.split("_")[-1])
    await state.update_data(lesson_count=count)
    data = await state.get_data()
    cash_student_id = data.get('cash_student_id')
    price = "4000₽" if count == 12 else "8000₽"
    desc = "12 занятий" if count == 12 else "Безлимит"
    await state.update_data(lesson_count=count)
    if cash_student_id:
        result = await add_abon(
            student_id=cash_student_id,
            lessons_count=count,
            session=session
        )
        desc = "12 занятий" if count == 12 else "Безлимит"
        if result:
            new_expire, _ = result
            await callback.message.edit_text(f"✅ Оплата наличными ({desc}) принята до {new_expire}")
        await state.clear()
        await callback.answer()
        return
    await state.set_state(PaymentStates.waiting_for_receipt)
    await callback.message.edit_text(
        f"💳 <b>Реквизиты (Детский - {desc}):</b>\n\n"
        f"Сумма к оплате: {price}\n"
        "СПБ: `+79606666165` (Адам.О)\n\n"
        "Пришлите скриншот чека:",
        parse_mode="HTML"
     )
    await callback.answer()


@router.message(PaymentStates.waiting_for_receipt, F.photo)
async def handle_receipt_submission(message: types.Message, state: FSMContext):
    data = await state.get_data()
    student_id = data.get("chosen_student_id")
    sport_type = data.get("sport_type", "не указан")
    lessons_count = data.get('lesson_count', 0)
    async with AsyncSessionLocal() as session:
        student = await session.get(Student, student_id)
        student_name = student.name if student else "Неизвестный"
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Оформить", callback_data=f"adm_confirm_{student_id}_{lessons_count}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_decline_{message.from_user.id}"))
    desc = "12 занятий" if lessons_count == 12 else "Безлимит"
    await message.bot.send_photo(
        chat_id=ADMIN_IDS[0],
        photo=message.photo[-1].file_id,
        caption=(
            f"<b>💰 Новая оплата!</b>\n"
            f"👤 Отправитель: @{message.from_user.username or 'без юзернейма'}\n"
            f"🥋 За кого: {student_name} (ID: {student_id})\n"
            f"🥊 Направление: {sport_type.upper()}"
            f"📊 Тип: <b>{desc}</b>"
        ),
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await state.clear()
    await message.answer("✅ Чек отправлен тренеру на проверку. Я пришлю уведомление, когда абонемент будет продлен.")


@router.callback_query(F.data.startswith("adm_confirm_"))
async def admin_confirm_pay(callback: types.CallbackQuery, session: AsyncSession):
    parts = callback.data.split('_')
    student_id = int(parts[2])
    count = int(parts[3])
    result = await add_abon(student_id, lessons_count=count, session=session)
    if result:
        new_expire, parent_id = result
        desc = "12 занятий" if count == 12 else "Безлимит"
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="👤 Мой профиль", callback_data="profile"))
        await callback.message.edit_caption(
            caption=callback.message.caption + f"<b>\n\n✅ ОФОРМЛЕНО: {desc} до {new_expire}</b>",
            parse_mode="HTML"
        )
        if parent_id and parent_id != 0:
            try:
                await callback.bot.send_message(
                    chat_id=parent_id,
                    text=(
                        f"<b>💳 Ваша оплата подтверждена!</b>\n\n"
                        f"Тип: <b>{desc}</b>\n"
                        f"Абонемент продлен до: <b>{new_expire}</b>\n\n"
                        f"Спасибо, что вы с нами! 🙏"
                    ),
                    reply_markup=kb.as_markup(),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление: {e}")
    else:
        await callback.answer("Ошибка: студент не найден", show_alert=True)


@router.callback_query(F.data.startswith("adm_decline_"))
async def admin_decline_pay(callback: types.CallbackQuery):
    user_id = int(callback.data.split("_")[-1])
    await callback.message.edit_caption(caption=callback.message.caption + "<b>\n\n❌ ОТКЛОНЕНО</b>")
    await callback.bot.send_message(
        chat_id=user_id,
        text="❌ Ваш чек отклонен. Пожалуйста, проверьте данные или свяжитесь с тренером."
    )


@router.callback_query(F.data == "admin_cash_list")
async def show_all_students_for_cash(callback: types.CallbackQuery):
    async with AsyncSessionLocal() as session:
        stmt = select(Student).order_by(Student.name)
        result = await session.execute(stmt)
        students = result.scalars().all()
    if not students:
        return await callback.answer("В базе пока пусто", show_alert=True)
    builder = InlineKeyboardBuilder()
    for s in students:
        builder.row(InlineKeyboardButton(text=f"👤 {s.name}", callback_data=f"cash_pay_{s.id}"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="admin"))
    await callback.message.edit_text("Выберите атлета для оплаты наличными:", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("cash_pay_"))
async def process_cash_payment(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    data_parts = callback.data.split("_")
    student_id = int(data_parts[-1])

    data = await state.get_data()
    sport_type = data.get('sport_type')

    await state.update_data(cash_student_id=student_id)
    if sport_type == 'bjj':
        result = await add_abon(student_id, lessons_count=0, session=session)

        if result:
            new_expire, parent_id = result
            await callback.message.edit_text(
                f"✅ <b>Оплата наличными (Взрослый/Безлимит) принята!</b>\n"
                f"Продлено до: <b>{new_expire}</b>",
                parse_mode="HTML"
            )

            if parent_id and parent_id != 0:
                try:
                    await callback.bot.send_message(
                        chat_id=parent_id,
                        text=f"💵 <b>Ваша оплата подтверждена!</b>\n"
                             f"Абонемент продлен до: <b>{new_expire}</b>.",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"Не удалось отправить уведомление: {e}")

            await state.clear()
        else:
            await callback.answer("Ошибка: студент не найден", show_alert=True)

    else:
        await callback.message.edit_text(
            "Выберите тип абонемента для ребенка (Наличные):",
            reply_markup=kids_pay_options().as_markup()
        )
        await callback.answer()
