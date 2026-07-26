from __future__ import annotations

from datetime import datetime
from html import escape

from aiogram import Bot
from sqlalchemy import select

from database.db import Club, ClubStaff, User


def format_order_items(items, *, product_only: bool = False) -> str:
    lines: list[str] = []
    for item in items:
        if product_only and not getattr(item, "product_id", None):
            continue
        title = escape(str(getattr(item, "title", "Без названия")))
        quantity = int(getattr(item, "quantity", 1) or 1)
        lines.append(f"• {title} × {quantity}")
    return "\n".join(lines) if lines else "• Без товарных позиций"


async def resolve_user_label(session, user_id: int | None, *, empty_label: str = "Наличная продажа") -> str:
    if not user_id:
        return empty_label
    user = await session.get(User, user_id)
    if user and getattr(user, "full_name", None):
        return f"{escape(user.full_name)} (<code>{user.user_id}</code>)"
    return f"<code>{user_id}</code>"


def build_owner_receipt_text(
    *,
    title: str,
    order_id: str,
    buyer_label: str,
    items_text: str,
    amount_kopecks: int,
    extra_lines: list[str] | None = None,
) -> str:
    lines = [
        f"✅ <b>{escape(title)}</b>",
        f"Заказ: <code>{escape(str(order_id))}</code>",
        f"Плательщик: {buyer_label}",
        items_text,
        f"Сумма: <b>{amount_kopecks / 100:.2f} ₽</b>",
        f"Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
    ]
    if extra_lines:
        lines.extend(extra_lines)
    return "\n".join(lines)


def build_staff_alert_text(
    *,
    title: str,
    order_id: str,
    items_text: str,
    amount_kopecks: int,
    buyer_label: str | None = None,
    badge: str = "🚨",
) -> str:
    lines = [
        f"{badge} <b>{escape(title)}</b>",
        f"Заказ: <code>{escape(str(order_id))}</code>",
    ]
    if buyer_label:
        lines.append(f"Плательщик: {buyer_label}")
    lines.extend(
        [
            items_text,
            f"Сумма: <b>{amount_kopecks / 100:.2f} ₽</b>",
            f"Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        ]
    )
    return "\n".join(lines)


async def notify_product_staff(bot: Bot, club: Club, session, text: str) -> None:
    staff_rows = (
        await session.execute(
            select(ClubStaff).where(
                ClubStaff.club_id == club.id,
                ClubStaff.is_active.is_(True),
            )
        )
    ).scalars().all()
    targets = [
        staff.telegram_id
        for staff in staff_rows
        if str(getattr(staff, "role", "")).strip().casefold() == "cashier"
    ]
    for telegram_id in targets:
        try:
            await bot.send_message(
                chat_id=int(telegram_id),
                text=text,
                parse_mode="HTML",
                disable_notification=False,
            )
        except Exception:
            continue


async def notify_stock_reminders(bot: Bot, club: Club, session, text: str) -> None:
    staff_rows = (
        await session.execute(
            select(ClubStaff).where(
                ClubStaff.club_id == club.id,
                ClubStaff.is_active.is_(True),
            )
        )
    ).scalars().all()
    targets = [
        int(staff.telegram_id)
        for staff in staff_rows
        if str(getattr(staff, "role", "")).strip().casefold() == "cashier"
    ]
    if club.owner_id:
        targets.append(int(club.owner_id))
    for telegram_id in dict.fromkeys(targets):
        try:
            await bot.send_message(
                chat_id=telegram_id,
                text=text,
                parse_mode="HTML",
                disable_notification=False,
            )
        except Exception:
            continue
