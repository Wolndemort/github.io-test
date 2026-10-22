"""Direct YooKassa billing for the SaaS platform license only."""
from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import select
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import SUPER_YOOKASSA_SECRET_KEY, SUPER_YOOKASSA_SHOP_ID, PROXY_URL
from database.db import AsyncSessionLocal, Club, SaaSPaymentOrder
from services.bot_registry import bots_dict
from services.yookassa_client import YooKassaClient


async def _send_payment_fallback(session, club: Club, now: datetime) -> None:
    """Create one fresh card-payment link and send it after auto-renewal fails."""
    recent = (await session.execute(
        select(SaaSPaymentOrder)
        .where(
            SaaSPaymentOrder.club_id == club.id,
            SaaSPaymentOrder.id.like("SAAS_FALLBACK_%"),
            SaaSPaymentOrder.created_at >= now - timedelta(hours=24),
        )
        .order_by(SaaSPaymentOrder.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if recent:
        return

    bot = bots_dict.get(club.bot_token)
    if not bot:
        return
    latest = (await session.execute(
        select(SaaSPaymentOrder)
        .where(SaaSPaymentOrder.club_id == club.id, SaaSPaymentOrder.status == "CONFIRMED")
        .order_by(SaaSPaymentOrder.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if not latest:
        return

    order = SaaSPaymentOrder(
        id=f"SAAS_FALLBACK_{uuid.uuid4().hex[:24].upper()}",
        club_id=club.id,
        owner_id=club.owner_id,
        amount_kopecks=700000,
        days=30,
        status="NEW",
    )
    session.add(order)
    await session.flush()
    try:
        bot_username = (await bot.get_me()).username or ""
        payment = await YooKassaClient(
            shop_id=SUPER_YOOKASSA_SHOP_ID,
            secret_key=SUPER_YOOKASSA_SECRET_KEY,
            proxy_url=PROXY_URL,
        ).init_payment(
            order_id=order.id,
            amount_kopecks=order.amount_kopecks,
            user_id=club.owner_id,
            bot_username=bot_username,
            payment_method_type="bank_card",
        )
        if not payment.get("Success"):
            order.status = "FAILED"
            await session.commit()
            return
        order.provider_payment_id = payment.get("PaymentId")
        await session.commit()
        await bot.send_message(
            club.owner_id,
            f"⚠️ Не удалось автоматически продлить лицензию CRM клуба «{club.name}».\n\n"
            "Нажмите кнопку ниже, чтобы оплатить 1 месяц и продолжить работу.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="💳 Оплатить 1 месяц — 7 000 ₽", url=payment["PaymentURL"]),
            ]]),
            parse_mode="HTML",
        )
    except Exception:
        order.status = "FAILED"
        await session.commit()


async def process_saas_auto_renewals() -> None:
    if not SUPER_YOOKASSA_SHOP_ID or not SUPER_YOOKASSA_SECRET_KEY:
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Club).where(
                Club.saas_auto_renew.is_(True),
                Club.saas_rebill_id.is_not(None),
                Club.subscription_expire_at <= now,
            ).with_for_update(skip_locked=True)
        )
        clubs = list(result.scalars().all())
        for club in clubs:
            latest = (await session.execute(
                select(SaaSPaymentOrder)
                .where(SaaSPaymentOrder.club_id == club.id, SaaSPaymentOrder.status == "CONFIRMED")
                .order_by(SaaSPaymentOrder.created_at.desc()).limit(1)
            )).scalar_one_or_none()
            if not latest:
                continue
            order = SaaSPaymentOrder(
                id=f"SAAS_REC_{uuid.uuid4().hex[:24].upper()}",
                club_id=club.id,
                owner_id=latest.owner_id,
                amount_kopecks=latest.amount_kopecks,
                days=latest.days,
                status="NEW",
                auto_renew=True,
            )
            session.add(order)
            await session.flush()
            result = await YooKassaClient(
                shop_id=SUPER_YOOKASSA_SHOP_ID,
                secret_key=SUPER_YOOKASSA_SECRET_KEY,
                proxy_url=PROXY_URL,
            ).charge_payment(
                order_id=order.id,
                amount_kopecks=order.amount_kopecks,
                payment_method_id=club.saas_rebill_id,
                club_name=club.name,
            )
            if result.get("Success"):
                order.provider_payment_id = result.get("PaymentId")
                if result.get("Status") == "succeeded":
                    order.status = "CONFIRMED"
                    club.subscription_expire_at = now + timedelta(days=order.days)
                elif result.get("Status") == "failed":
                    order.status = "FAILED"
                await session.commit()
                if order.status == "FAILED":
                    await _send_payment_fallback(session, club, now)
            else:
                order.status = "FAILED"
                await session.commit()
                await _send_payment_fallback(session, club, now)
