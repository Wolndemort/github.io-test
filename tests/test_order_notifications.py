from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.order_notifications import (
    build_owner_receipt_text,
    build_staff_alert_text,
    format_order_items,
    notify_product_staff,
    resolve_user_label,
)


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, parse_mode=None, disable_notification=None):
        self.sent.append(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_notification": disable_notification,
            }
        )


@pytest.mark.asyncio
async def test_receipt_helpers_include_buyer_name_and_product_alert_text():
    session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(full_name="Иван Петров", user_id=77)))
    buyer = await resolve_user_label(session, 77)
    owner_text = build_owner_receipt_text(
        title="Новая оплата",
        order_id="CART_1",
        buyer_label=buyer,
        items_text=format_order_items([SimpleNamespace(title="Латте", quantity=2, product_id=1)]),
        amount_kopecks=25000,
    )
    staff_text = build_staff_alert_text(
        title="Новый товарный заказ",
        order_id="CART_1",
        buyer_label=buyer,
        items_text=format_order_items([SimpleNamespace(title="Латте", quantity=2, product_id=1)], product_only=True),
        amount_kopecks=25000,
        badge="☕",
    )

    assert "Иван Петров" in owner_text
    assert "Плательщик" in owner_text
    assert "Латте × 2" in owner_text
    assert "☕" in staff_text
    assert "Латте × 2" in staff_text


@pytest.mark.asyncio
async def test_notify_product_staff_sends_only_to_active_cash_staff():
    from database.db import ClubStaff

    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=FakeResult(
                [
                    SimpleNamespace(telegram_id=101, role="cashier", permissions={}, is_active=True, club_id=1),
                    SimpleNamespace(telegram_id=102, role="coach", permissions={}, is_active=True, club_id=1),
                    SimpleNamespace(telegram_id=103, role="manager", permissions={}, is_active=False, club_id=1),
                ]
            )
        )
    )
    bot = FakeBot()
    club = SimpleNamespace(id=1, name="Клуб")

    await notify_product_staff(bot, club, session, "🚨 <b>Новый товарный заказ</b>")

    assert [x["chat_id"] for x in bot.sent] == [101]
    assert bot.sent[0]["disable_notification"] is False
    assert bot.sent[0]["parse_mode"] == "HTML"
