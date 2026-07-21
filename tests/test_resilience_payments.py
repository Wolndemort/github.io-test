from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from admin_module.payments_webhook import yookassa_webhook
from services.abuse_guard import rate_limit


class BrokenRedis:
    async def incr(self, key):
        raise ConnectionError("Redis unavailable")


@pytest.mark.asyncio
async def test_rate_limit_fails_closed_when_redis_is_down():
    assert await rate_limit(BrokenRedis(), "idem:test", 1, 90) is False


@pytest.mark.asyncio
async def test_repeated_confirmed_webhook_is_idempotent():
    request = SimpleNamespace(
        json=AsyncMock(
            return_value={
                "event": "payment.succeeded",
                "object": {
                    "status": "succeeded",
                    "amount": {"currency": "RUB"},
                    "metadata": {"order_id": "WEB_TEST"},
                    "id": "payment_test",
                },
            }
        )
    )
    order = SimpleNamespace(status="CONFIRMED")
    result = SimpleNamespace(scalar_one_or_none=lambda: order)
    session = SimpleNamespace(execute=AsyncMock(return_value=result))

    response = await yookassa_webhook(request, session)

    assert response == {"status": "ok"}
    session.execute.assert_awaited_once()

