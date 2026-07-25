import httpx
from html import escape
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_module.router_base import router
from config import PROXY_URL
from database.db import PaymentOrder, CartOrder, CartItem, ClubProduct, Club, Student, Subscription, add_abon, purchase_student_freeze, get_session
from loguru import logger
from services.audit import audit_event
from services.yookassa_client import YooKassaClient
from services.order_notifications import (
    build_owner_receipt_text,
    build_staff_alert_text,
    format_order_items,
    notify_product_staff,
    resolve_user_label,
)
from aiogram import Bot


@router.post("/v1/payments/yookassa/webhook")
async def yookassa_webhook(request: Request, session: AsyncSession = Depends(get_session)):
    payload = await request.json()
    event = payload.get("event")
    object_data = payload.get("object", {})
    if event != "payment.succeeded" or object_data.get("status") != "succeeded":
        return {"status": "ignored"}
    amount_data = object_data.get("amount") or {}
    if amount_data.get("currency") != "RUB":
        return {"status": "ignored"}
    metadata = object_data.get("metadata", {})
    order_id = metadata.get("order_id")
    if not order_id:
        return {"status": "ignored"}
    payment_id = object_data.get("id")
    if not payment_id:
        return {"status": "ignored"}

    if str(order_id).startswith("CART_"):
        cart = (await session.execute(select(CartOrder).where(CartOrder.id == order_id).with_for_update())).scalar_one_or_none()
        if not cart or cart.status == "CONFIRMED": return {"status": "ok" if cart else "ignored"}
        club = await session.get(Club, cart.club_id); pay = (club.club_settings or {}).get("payments", {}) if club else {}
        try:
            async with httpx.AsyncClient(auth=(pay.get("yookassa_shop_id"), pay.get("yookassa_secret_key")), timeout=10) as client:
                vr = await client.get(f"https://api.yookassa.ru/v3/payments/{payment_id}")
            vp = vr.json()
            amount = int(Decimal(str(vp.get("amount", {}).get("value"))) * 100)
            if vr.status_code != 200 or vp.get("status") != "succeeded" or vp.get("metadata", {}).get("order_id") != order_id or amount != cart.amount_kopecks:
                return {"status": "ignored"}
        except (httpx.HTTPError, InvalidOperation, TypeError, ValueError):
            return {"status": "retry"}
        items = (await session.execute(select(CartItem).where(CartItem.cart_order_id == cart.id))).scalars().all()
        for item in items:
            if item.product_id:
                product = await session.get(ClubProduct, item.product_id, with_for_update=True)
                if not product or product.stock < item.quantity: cart.status = "FAILED"; await session.commit(); return {"status": "ok"}
                product.stock -= item.quantity
            elif item.item_type == "subscription":
                p = item.payload or {}
                for _ in range(max(1, int(item.quantity or 1))):
                    await add_abon(student_id=int(p["student_id"]), lessons_count=int(p.get("count", 0)), session=session, club_id=cart.club_id, club_settings=club.club_settings or {}, days_to_add=int(p.get("days", 30)), discipline=p.get("discipline"))
            elif item.item_type == "freeze":
                p = item.payload or {}
                for _ in range(max(1, int(item.quantity or 1))):
                    await purchase_student_freeze(int(p["student_id"]), cart.club_id, int(p["days"]), session)
        cart.status = "CONFIRMED"; cart.provider_payment_id = payment_id
        await session.commit()
        buyer_label = await resolve_user_label(session, cart.user_id)
        receipt = format_order_items(items)
        notice = build_owner_receipt_text(
            title="Новая оплата",
            order_id=cart.id,
            buyer_label=buyer_label,
            items_text=receipt,
            amount_kopecks=cart.amount_kopecks,
        )
        barista_items = format_order_items(items, product_only=True)
        barista_alert = None
        if "Без товарных позиций" not in barista_items:
            barista_alert = build_staff_alert_text(
                title="Новый товарный заказ",
                order_id=cart.id,
                buyer_label=buyer_label,
                items_text=barista_items,
                amount_kopecks=cart.amount_kopecks,
                badge="☕",
            )
        try:
            bot = Bot(club.bot_token)
            if club.owner_id:
                await bot.send_message(club.owner_id, notice, parse_mode="HTML")
            if barista_alert:
                await notify_product_staff(bot, club, session, barista_alert)
            if cart.user_id:
                await bot.send_message(
                    cart.user_id,
                    notice.replace("✅ <b>Новая оплата</b>", "✅ <b>Оплата подтверждена</b>"),
                    parse_mode="HTML",
                )
            await bot.session.close()
        except Exception:
            logger.exception("Не удалось отправить уведомление по корзине %s", cart.id)
        audit_event("cart_payment_confirmed", club_id=cart.club_id, order_id=cart.id, amount_kopecks=cart.amount_kopecks)
        return {"status": "ok"}

    order_result = await session.execute(select(PaymentOrder).where(PaymentOrder.id == order_id).with_for_update())
    order = order_result.scalar_one_or_none()
    if not order:
        return {"status": "ignored"}
    if order.status == "CONFIRMED":
        return {"status": "ok"}

    club_result = await session.execute(select(Club).where(Club.id == order.club_id))
    payment_club = club_result.scalar_one_or_none()
    pay_cfg = (payment_club.club_settings or {}).get("payments", {}) if payment_club else {}
    shop_id = pay_cfg.get("yookassa_shop_id")
    secret_key = pay_cfg.get("yookassa_secret_key")
    if not shop_id or not secret_key:
        return {"status": "ignored"}
    try:
        async with httpx.AsyncClient(auth=(shop_id, secret_key), timeout=10.0) as client:
            verify_response = await client.get(f"https://api.yookassa.ru/v3/payments/{payment_id}")
        if verify_response.status_code != 200:
            return {"status": "ignored"}
        verified_payment = verify_response.json()
        if verified_payment.get("status") != "succeeded" or verified_payment.get("metadata", {}).get("order_id") != str(order.id):
            return {"status": "ignored"}
    except httpx.HTTPError:
        logger.exception("Не удалось проверить платеж %s через API ЮKassa", payment_id)
        return {"status": "retry"}

    try:
        received_amount = int(Decimal(str(verified_payment.get("amount", {}).get("value"))) * 100)
    except (InvalidOperation, TypeError, ValueError):
        return {"status": "ignored"}
    if verified_payment.get("amount", {}).get("currency") != "RUB" or received_amount != order.amount_kopecks:
        return {"status": "ignored"}

    if order.status != "CONFIRMED":
        order.status = "CONFIRMED"
        payment_method = object_data.get("payment_method", {})
        payment_method_id = payment_method.get("id")
        saved_card_flag = payment_method.get("saved", False)

        if order.type == "FIRST" and payment_method_id and saved_card_flag:
            sub_result = await session.execute(
                select(Subscription)
                .where(Subscription.student_id == order.student_id, Subscription.club_id == order.club_id)
                .with_for_update()
            )
            subscription = sub_result.scalar_one_or_none()
            next_charge_naive = (datetime.now(timezone.utc) + timedelta(days=30)).replace(tzinfo=None)
            if subscription:
                subscription.rebill_id = str(payment_method_id)
                subscription.next_charge_at = next_charge_naive
                subscription.is_active = True
                subscription.amount_kopecks = order.amount_kopecks
            else:
                session.add(Subscription(user_id=order.user_id, student_id=order.student_id, club_id=order.club_id, rebill_id=str(payment_method_id), amount_kopecks=order.amount_kopecks, next_charge_at=next_charge_naive, is_active=True))

        club_result = await session.execute(select(Club).where(Club.id == order.club_id))
        club = club_result.scalar_one_or_none()
        club_settings = club.club_settings if club else {}
        if order.type.startswith("FREEZE"):
            abon_result = await purchase_student_freeze(order.student_id, order.club_id, order.days_to_add, session)
        else:
            abon_result = await add_abon(student_id=order.student_id, lessons_count=order.lesson_count, session=session, club_id=order.club_id, club_settings=club_settings, days_to_add=order.days_to_add, discipline=order.discipline)
        await session.commit()
        audit_event("yookassa_webhook_confirmed", club_id=order.club_id, order_id=order.id, student_id=order.student_id, amount_kopecks=order.amount_kopecks, order_type=order.type)
        if club and club.owner_id:
            try:
                frozen_student = await session.get(Student, order.student_id)
                payer_label = await resolve_user_label(session, order.user_id, empty_label="Не указан")
                bot = Bot(club.bot_token)
                is_freeze = order.type.startswith("FREEZE")
                await bot.send_message(
                    club.owner_id,
                    (
                        ("❄️ <b>Клиент купил заморозку</b>" if is_freeze else "✅ <b>Новая оплата абонемента</b>") + "\n\n"
                        f"Плательщик: {payer_label}\n"
                        f"Атлет: <b>{escape(frozen_student.name if frozen_student else str(order.student_id))}</b>\n"
                        f"Сумма: <b>{order.amount_kopecks / 100:.2f} ₽</b>\n"
                        + (f"Срок: <b>{order.days_to_add} дн.</b>\n" if is_freeze else f"Занятий: <b>{order.lesson_count}</b>\n")
                        + f"Дата окончания: <b>{frozen_student.expire_date.strftime('%d.%m.%Y') if frozen_student and frozen_student.expire_date else '—'}</b>"
                    ),
                    parse_mode="HTML",
                )
                await bot.session.close()
            except Exception:
                logger.exception("Не удалось уведомить владельца о платной заморозке %s", order.id)
    return {"status": "ok"}
