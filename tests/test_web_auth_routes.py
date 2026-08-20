from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from auth import routes
from auth.routes import TelegramExchangePayload, auth_logout, auth_me, telegram_exchange
from auth.web_session import CSRF_HEADER, SESSION_COOKIE, get_web_session


class FakeRedis:
    def __init__(self):
        self.values = {}

    async def setex(self, key, ttl, value):
        self.values[key] = value

    async def get(self, key):
        return self.values.get(key)

    async def expire(self, key, ttl):
        return None

    async def delete(self, key):
        self.values.pop(key, None)


class FakeDb:
    def __init__(self, club):
        self.club = club

    async def scalar(self, _query):
        return self.club


def request(redis, cookies=None):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(redis_client=redis)),
        cookies=cookies or {},
        headers={},
    )


@pytest.mark.asyncio
async def test_telegram_exchange_creates_web_session_without_exposing_init_data(monkeypatch):
    redis = FakeRedis()
    club = SimpleNamespace(id=7, bot_token="bot-token", owner_id=42)
    monkeypatch.setattr(routes, "verify_telegram_data", lambda init_data, token: {"id": 42, "first_name": "Owner"})

    response = await telegram_exchange(
        TelegramExchangePayload(init_data="secret-init-data", club_id=7),
        request(redis),
        FakeDb(club),
    )

    assert response.status_code == 200
    assert "secret-init-data" not in response.body.decode()
    cookie = next(value for key, value in response.raw_headers if key == b"set-cookie")
    session_id = cookie.decode().split("=", 1)[1].split(";", 1)[0]
    context = await get_web_session(redis, request(redis, {SESSION_COOKIE: session_id}))
    assert context.user_id == 42
    assert context.club_id == 7
    assert context.actor_type == "owner"
    csrf_cookie = next(value for key, value in response.raw_headers if key == b"set-cookie" and b"speedycrm_csrf_token" in value)
    csrf_token = csrf_cookie.decode().split("=", 1)[1].split(";", 1)[0]
    assert csrf_token


@pytest.mark.asyncio
async def test_telegram_exchange_rejects_invalid_telegram_data(monkeypatch):
    club = SimpleNamespace(id=7, bot_token="bot-token", owner_id=42)
    monkeypatch.setattr(routes, "verify_telegram_data", lambda init_data, token: None)

    with pytest.raises(HTTPException) as error:
        await telegram_exchange(
            TelegramExchangePayload(init_data="bad", club_id=7),
            request(FakeRedis()),
            FakeDb(club),
        )

    assert error.value.status_code == 401
    assert error.value.detail["code"] == "invalid_telegram_auth"


@pytest.mark.asyncio
async def test_telegram_exchange_resolves_staff_permissions_for_selected_club(monkeypatch):
    redis = FakeRedis()
    club = SimpleNamespace(id=7, bot_token="bot-token", owner_id=42)
    staff = SimpleNamespace(role="manager", permissions={"allow": ["analytics_view"]}, is_active=True, club_id=7, telegram_id=99)
    class StaffDb(FakeDb):
        def __init__(self): self.calls=0
        async def scalar(self, query):
            self.calls += 1
            return club if self.calls == 1 else staff
    monkeypatch.setattr(routes, "verify_telegram_data", lambda init_data, token: {"id": 99})
    response = await telegram_exchange(TelegramExchangePayload(init_data="one-time", club_id=7), request(redis), StaffDb())
    assert response.status_code == 200
    assert b"one-time" not in response.body


@pytest.mark.asyncio
async def test_auth_me_requires_a_web_session():
    with pytest.raises(HTTPException) as error:
        await auth_me(None)

    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_logout_deletes_cookie_and_revokes_session():
    redis = FakeRedis()
    session_id = "session-id"
    redis.values["web_session:" + session_id] = '{"csrf_token":"csrf-value"}'
    req = request(redis, {SESSION_COOKIE: session_id})
    req.headers[CSRF_HEADER] = "csrf-value"
    response = await auth_logout(req)

    assert response.status_code == 200
    assert "web_session:session-id" not in redis.values
    assert 'speedycrm_web_session=""' in response.headers["set-cookie"]


@pytest.mark.asyncio
async def test_logout_rejects_missing_or_invalid_csrf():
    redis = FakeRedis()
    redis.values["web_session:session-id"] = '{"csrf_token":"csrf-value"}'
    with pytest.raises(HTTPException) as error:
        await auth_logout(request(redis, {SESSION_COOKIE: "session-id"}))
    assert error.value.status_code == 403
    assert error.value.detail["code"] == "csrf_failed"
