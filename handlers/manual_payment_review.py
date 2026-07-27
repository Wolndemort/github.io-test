from __future__ import annotations

from aiogram import Router, types
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import PaymentOrder, CartOrder, CartItem, ClubProduct, Student, add_abon, purchase_student_freeze
from services.audit import audit_event
from services.order_notifications import build_owner_receipt_text, format_order_items, resolve_user_label

router = Router()


def _is_already_handled(text: str | None) -> bool:
    text = text or ""
    return any(marker in text for marker in ("✅", "❌"))


@router.callback_query(lambda c: c.data.startswith("manual_order_confirm_"))
async def manual_order_confirm(callback: types.CallbackQuery, session: AsyncSession, club, club_settings: dict):
    if callback.from_user.id != club.owner_id:
        return await callback.answer("❌ Вы не являетесь владельцем этого клуба!", show_alert=True)
    if _is_already_handled(callback.message.text or callback.message.caption):
        return await callback.answer("Эта заявка уже обработана", show_alert=True)

    order_id = callback.data.removeprefix("manual_order_confirm_")
    bot = callback.bot

    if order_id.startswith("CART_"):
        order = await session.get(CartOrder, order_id, with_for_update=True)
        if not order or order.status != "NEW":
            return await callback.answer("Заявка уже обработана или не найдена", show_alert=True)
        items = (await session.execute(select(CartItem).where(CartItem.cart_order_id == order.id))).scalars().all()
        for item in items:
            if item.product_id:
                product = await session.get(ClubProduct, item.product_id, with_for_update=True)
                if not product or product.stock < item.quantity:
                    order.status = "FAILED"
                    await session.commit()
                    return await callback.answer("Товар закончился или недоступен", show_alert=True)
                product.stock -= item.quantity
            elif item.item_type == "subscription":
                payload = item.payload or {}
                for _ in range(max(1, int(item.quantity or 1))):
                    await add_abon(
                        student_id=int(payload["student_id"]),
                        lessons_count=int(payload.get("count", 0)),
                        session=session,
                        club_id=club.id,
                        club_settings=club_settings or {},
                        days_to_add=int(payload.get("days", 30)),
                        discipline=payload.get("discipline"),
                    )
            elif item.item_type == "freeze":
                payload = item.payload or {}
                for _ in range(max(1, int(item.quantity or 1))):
                    await purchase_student_freeze(int(payload["student_id"]), club.id, int(payload["days"]), session)
        order.status = "CONFIRMED"
        order.provider_payment_id = order.provider_payment_id or "MANUAL:APPROVED"
        await session.commit()
        buyer_label = await resolve_user_label(session, order.user_id)
        owner_text = build_owner_receipt_text(
            title="Ручная оплата подтверждена",
            order_id=order.id,
            buyer_label=buyer_label,
            items_text=format_order_items(items),
            amount_kopecks=order.amount_kopecks,
            extra_lines=["Способ: <b>Реквизиты</b>"],
        )
        user_text = owner_text.replace("Ручная оплата подтверждена", "Оплата подтверждена")
        if order.user_id:
            await bot.send_message(order.user_id, user_text, parse_mode="HTML")
        audit_event("manual_order_confirmed", club_id=club.id, actor_user_id=callback.from_user.id, actor_role="owner", action="confirm", object_type="cart_order", object_id=order.id, amount_kopecks=order.amount_kopecks, method="manual")
    else:
        order = await session.get(PaymentOrder, order_id, with_for_update=True)
        if not order or order.status != "NEW":
            return await callback.answer("Заявка уже обработана или не найдена", show_alert=True)
        if order.type.startswith("FREEZE"):
            await purchase_student_freeze(order.student_id, club.id, order.days_to_add, session)
        else:
            await add_abon(
                student_id=order.student_id,
                lessons_count=order.lesson_count,
                session=session,
                club_id=club.id,
                club_settings=club_settings or {},
                days_to_add=order.days_to_add,
                discipline=order.discipline,
            )
        order.status = "CONFIRMED"
        order.provider_payment_id = order.provider_payment_id or "MANUAL:APPROVED"
        await session.commit()
        student = await session.get(Student, order.student_id)
        buyer_label = await resolve_user_label(session, order.user_id)
        owner_text = build_owner_receipt_text(
            title="Ручная оплата подтверждена",
            order_id=order.id,
            buyer_label=buyer_label,
            items_text=f"• {student.name if student else order.student_id}",
            amount_kopecks=order.amount_kopecks,
            extra_lines=["Способ: <b>Реквизиты</b>"],
        )
        user_text = owner_text.replace("Ручная оплата подтверждена", "Оплата подтверждена")
        if order.user_id:
            await bot.send_message(order.user_id, user_text, parse_mode="HTML")
        audit_event("manual_order_confirmed", club_id=club.id, actor_user_id=callback.from_user.id, actor_role="owner", action="confirm", object_type="payment_order", object_id=order.id, amount_kopecks=order.amount_kopecks, method="manual")

    try:
        await callback.message.edit_text(
            (callback.message.text or callback.message.caption or "") + "\n\n✅ <b>ОДОБРЕНО</b>",
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Заявка подтверждена")


@router.callback_query(lambda c: c.data.startswith("manual_order_decline_"))
async def manual_order_decline(callback: types.CallbackQuery, session: AsyncSession, club, club_settings: dict):
    if callback.from_user.id != club.owner_id:
        return await callback.answer("❌ Вы не являетесь владельцем этого клуба!", show_alert=True)
    if _is_already_handled(callback.message.text or callback.message.caption):
        return await callback.answer("Эта заявка уже обработана", show_alert=True)

    order_id = callback.data.removeprefix("manual_order_decline_")
    bot = callback.bot

    if order_id.startswith("CART_"):
        order = await session.get(CartOrder, order_id, with_for_update=True)
        if not order or order.status != "NEW":
            return await callback.answer("Заявка уже обработана или не найдена", show_alert=True)
        order.status = "REJECTED"
        await session.commit()
        if order.user_id:
            await bot.send_message(order.user_id, f"❌ <b>Оплата по реквизитам отклонена</b>\n\nКлуб: <b>{club.name}</b>\n\nПроверьте реквизиты или свяжитесь с администратором.", parse_mode="HTML")
        audit_event("manual_order_declined", club_id=club.id, actor_user_id=callback.from_user.id, actor_role="owner", action="decline", object_type="cart_order", object_id=order.id, amount_kopecks=order.amount_kopecks, method="manual")
    else:
        order = await session.get(PaymentOrder, order_id, with_for_update=True)
        if not order or order.status != "NEW":
            return await callback.answer("Заявка уже обработана или не найдена", show_alert=True)
        order.status = "REJECTED"
        await session.commit()
        if order.user_id:
            await bot.send_message(order.user_id, f"❌ <b>Оплата по реквизитам отклонена</b>\n\nКлуб: <b>{club.name}</b>\n\nПроверьте реквизиты или свяжитесь с администратором.", parse_mode="HTML")
        audit_event("manual_order_declined", club_id=club.id, actor_user_id=callback.from_user.id, actor_role="owner", action="decline", object_type="payment_order", object_id=order.id, amount_kopecks=order.amount_kopecks, method="manual")

    try:
        await callback.message.edit_text(
            (callback.message.text or callback.message.caption or "") + "\n\n❌ <b>ОТКЛОНЕНО</b>",
            parse_mode="HTML",
        )
    except Exception:
        await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Заявка отклонена")
