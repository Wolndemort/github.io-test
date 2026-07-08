import uuid
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

# Импортируем твои модели и наш настроенный клиент
from database.db import AsyncSessionLocal, User, PaymentOrder
from main import tbank  # Берем настроенный объект tbank из main.py

router = Router()


@router.message(Command("test_pay"))
async def test_payment_handler(message: Message):
    """Тестовый хендлер для проверки эквайринга Т-Банка с рекуррентом"""
    user_id = message.from_user.id

    # Сумма для теста (например, 100 рублей = 10000 копеек)
    amount_rub = 100
    amount_kopecks = amount_rub * 100

    # Сгенерируем временные ID для теста, пока нет кнопок в профиле
    order_id = f"INIT_{uuid.uuid4().hex[:12].upper()}"
    test_student_id = 1  # ID существующего студента в твоей БД для теста

    async with AsyncSessionLocal() as session:
        # Достаем клуб пользователя, чтобы привязать платеж
        user_res = await session.execute(select(User).where(User.user_id == user_id))
        user = user_res.scalar_one_or_none()

        if not user or not user.club_id:
            await message.answer("❌ Твой пользователь не привязан к клубу в БД.")
            return

        club_id = user.club_id

        # Фиксируем тестовый заказ в базе
        new_order = PaymentOrder(
            id=order_id,
            user_id=user_id,
            student_id=test_student_id,
            club_id=club_id,
            amount_kopecks=amount_kopecks,
            status="NEW",
            type="FIRST"  # Маркер первой оплаты для создания автоплатежа
        )
        session.add(new_order)
        await session.commit()

    # Делаем запрос в Т-Банк за ссылкой
    payment_data = await tbank.init_payment(
        order_id=order_id,
        amount_kopecks=amount_kopecks,
        user_id=user_id
    )

    if payment_data.get("Success"):
        payment_url = payment_data.get("PaymentURL")

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Тестовая оплата (100 ₽)", url=payment_url)]
        ])

        await message.answer(
            f"🤖 <b>Тест официальной оплаты Т-Банка</b>\n\n"
            f"Ссылка сгенерирована успешно. Нажми на кнопку ниже, чтобы перейти на шлюз Т-Кассы. "
            f"Используй данные тестовых карт Т-Банка для проверки автопривязки `RebillId`.",
            reply_markup=kb
        )
    else:
        error_msg = payment_data.get("Message", "Ошибка банка")
        await message.answer(f"❌ Банк вернул ошибку: {error_msg}")
