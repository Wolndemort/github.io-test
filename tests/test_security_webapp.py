import hashlib
import hmac
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
import time
from admin_module.api import verify_telegram_data
from admin_module.sqladmin import AdminAuth


def make_init_data(bot_token: str, user_id: int) -> str:
    values = {"auth_date": str(int(time.time())), "user": '{"id": %d}' % user_id}
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def test_telegram_init_data_accepts_valid_signature():
    payload = make_init_data("bot-token", 123)
    assert verify_telegram_data(payload, "bot-token")["id"] == 123


def test_telegram_init_data_rejects_tampered_user():
    payload = make_init_data("bot-token", 123).replace("%22id%22%3A+123", "%22id%22%3A+999")
    assert verify_telegram_data(payload, "bot-token") is None


def test_telegram_init_data_rejects_expired_signature():
    values = {"auth_date": "1700000000", "user": '{"id": 123}'}
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret = hmac.new(b"WebAppData", b"bot-token", hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    assert verify_telegram_data(urlencode(values), "bot-token") is None


@pytest.mark.asyncio
async def test_sqladmin_auth_rejects_missing_credentials(monkeypatch):
    monkeypatch.delenv("ADMIN_PANEL_USER", raising=False)
    monkeypatch.delenv("ADMIN_PANEL_PASSWORD", raising=False)
    request = SimpleNamespace(
        form=AsyncMock(return_value={"username": "admin", "password": "password"}),
        session={},
    )
    assert await AdminAuth(secret_key="test").login(request) is False
