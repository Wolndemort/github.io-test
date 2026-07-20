import os
import logging
from database.db import Club
from datetime import datetime, timedelta
from aiogram import Router, F, types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from redis.asyncio import Redis

logger = logging.getLogger(__name__)
router = Router()

# Забираем токен ЮKassa из переменных окружения (.env)
YOOKASSA_PROVIDER_TOKEN = os.getenv("YOOKASSA_PROVIDER_TOKEN", "ТВОЙ_ТОКЕН_ЮКАССЫ_ИЗ_BOTFATHER")


# =========================================================================
# БЛОК 1: ОТРИСОВКА МЕНЮ ВЫБОРА ТАРИФА (РУБЛИ)
# =========================================================================
@router.callback_query(F.data == "pay_menu")
async def show_pay_menu(callback: types.CallbackQuery):
    await callback.answer()

    builder = InlineKeyboardBuilder()
    # Зашиваем количество дней в callback_data
    builder.row(types.InlineKeyboardButton(
        text="🌙 1 месяц (30 дн.) — 1 500 ₽", callback_data="buy_sub_30")
    )
    builder.row(types.InlineKeyboardButton(
        text="☀️ 1 год (365 дн.) — 15 000 ₽", callback_data="buy_sub_365")
    )
    builder.row(types.InlineKeyboardButton(
        text="⬅️ Назад в админку", callback_data="admin")
    )
    builder.adjust(1)

    await callback.message.edit_text(
        text="💳 <b>Управление подпиской клуба</b>\n\n"
             "Выберите тарифный план для продления лицензии CRM.\n"
             "Оплата происходит безопасно через ЮKassa прямо внутри Telegram. "
             "Все функции СКУД разблокируются автоматически сразу после транзакции.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


# =========================================================================
# БЛОК 2: ГЕНЕРАЦИЯ И ОТПРАВКА ИНВОЙСА ЮKASSA
# =========================================================================
@router.callback_query(F.data.startswith("buy_sub_"))
async def send_subscription_invoice(callback: types.CallbackQuery, club: "Club"):
    await callback.answer()
    days = int(callback.data.split("_")[-1])

    # Математика цен в копейках для ЮKassa (1 рубль = 100 копеек)
    price_amount = 150000 if days == 30 else 1500000

    # Зашиваем ID клуба и количество дней
    invoice_payload = f"sub_yookassa:{club.id}:{days}"

    await callback.message.answer_invoice(
        title=f"Подписка CRM: {days} дней",
        description=f"Продление лицензии и доступа к СКУД для клуба «{club.name}»",
        prices=[types.LabeledPrice(label="Рубли", amount=price_amount)],
        provider_token=YOOKASSA_PROVIDER_TOKEN,  # Твой токен ЮKassa
        payload=invoice_payload,
        currency="RUB",  # Переключили на рубли
        start_parameter="club_pay"
    )


# =========================================================================
# БЛОК 3: ОТВЕТ НА PRE-CHECKOUT QUERY (ОБЯЗАТЕЛЬНО ДЛЯ ЮKASSA)
# =========================================================================
@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: types.PreCheckoutQuery):
    # ЮKassa и Telegram ждут этот ответ строго в течение 10 секунд
    await pre_checkout_query.answer(ok=True)


# =========================================================================
# БЛОК 4: ФИНАЛЬНОЕ ЗАЧИСЛЕНИЕ ДНЕЙ И СБРОС КЭША
# =========================================================================
@router.message(F.successful_payment)
async def on_successful_payment(message: types.Message, session: AsyncSession, redis: Redis):
    payload = message.successful_payment.invoice_payload

    # Проверяем, что платеж наш рублевый
    if not payload.startswith("sub_yookassa:"):
        return

    try:
        _, club_id_str, days_str = payload.split(":")
        target_club_id = int(club_id_str)
        days = int(days_str)
    except ValueError:
        logger.error(f"❌ Ошибка парсинга рублевого payload: {payload}")
        return

    # Пробиваем кэш SQLAlchemy сессии
    session.expire_all()
    club_res = await session.execute(select(Club).where(Club.id == target_club_id))
    club = club_res.scalar_one_or_none()

    if not club:
        logger.critical(f"🚨 Оплата ЮKassa пришла для несуществующего club_id: {target_club_id}")
        await message.answer("❌ Произошла системная ошибка. Обратитесь к разработчику платформы.")
        return

    # Наивное локальное время
    now_naive = datetime.now().replace(tzinfo=None)

    # Честная математика продления дат
    if club.subscription_expire_at and club.subscription_expire_at.replace(tzinfo=None) > now_naive:
        new_expire = club.subscription_expire_at.replace(tzinfo=None) + timedelta(days=days)
    else:
        new_expire = now_naive + timedelta(days=days)

    # Пишем новую дату в Postgres
    await session.execute(
        update(Club)
        .where(Club.id == club.id)
        .values(subscription_expire_at=new_expire)
    )
    await session.commit()

    # Стираем старый кэш в Redis, чтобы мидлварь увидела новую дату без перезапуска
    cache_key = f"club_config:{club.bot_token}"
    await redis.delete(cache_key)
    logger.info(f"🔥 ЮKassa: Кэш Redis успешно очищен для клуба {club.id} (Бот: {club.bot_token})")

    # Радуем босса
    await message.answer(
        text=f"✅ <b>Оплата успешно принята!</b>\n\n"
             f"Лицензия CRM для клуба <b>«{club.name}»</b> продлена на <b>{days} дней</b>.\n"
             f"Новая дата окончания подписки: <b>{new_expire.strftime('%d.%m.%Y')}</b>\n\n"
             f"<i>Все ограничения СКУД и админ-панели полностью сняты. Приятной работы!</i>",
        parse_mode="HTML"
    )
