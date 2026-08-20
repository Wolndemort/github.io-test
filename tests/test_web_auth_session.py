from types import SimpleNamespace

import pytest

from auth.context import AuthContext
from auth.web_session import (
    CSRF_HEADER,
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    create_web_session,
    get_web_session,
    revoke_web_session,
    validate_csrf,
)


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    async def setex(self, key, ttl, value):
        self.values[key] = value
        self.ttls[key] = ttl

    async def get(self, key):
        return self.values.get(key)

    async def expire(self, key, ttl):
        if key in self.values:
            self.ttls[key] = ttl

    async def delete(self, key):
        self.values.pop(key, None)
        self.ttls.pop(key, None)


def request_with(redis, cookies=None):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(redis_client=redis)),
        cookies=cookies or {},
        headers={},
    )


@pytest.mark.asyncio
async def test_web_session_is_server_side_and_round_trips_context():
    redis = FakeRedis()
    context = AuthContext(42, 7, "staff", "manager", frozenset({"cash_view"}), "telegram")

    session_id = await create_web_session(redis, context)

    assert session_id
    stored_payload = next(iter(redis.values.values()))
    assert session_id not in stored_payload
    assert list(redis.ttls.values()) == [SESSION_TTL_SECONDS]

    restored = await get_web_session(redis, request_with(redis, {SESSION_COOKIE: session_id}))
    assert restored == context
    assert list(redis.ttls.values()) == [SESSION_TTL_SECONDS]


@pytest.mark.asyncio
async def test_logout_revokes_server_side_session():
    redis = FakeRedis()
    context = AuthContext(42, 7, "owner", "owner", frozenset(), "telegram")
    session_id = await create_web_session(redis, context)
    request = request_with(redis, {SESSION_COOKIE: session_id})

    await revoke_web_session(redis, request)

    assert await get_web_session(redis, request) is None


@pytest.mark.asyncio
async def test_csrf_requires_header_matching_session_token():
    redis = FakeRedis()
    context = AuthContext(42, 7, "owner", "owner", frozenset(), "telegram")
    session_id = await create_web_session(redis, context)
    raw = next(iter(redis.values.values()))
    import json
    csrf_token = json.loads(raw)["csrf_token"]
    req = request_with(redis, {SESSION_COOKIE: session_id})

    assert await validate_csrf(redis, req) is False
    req.headers[CSRF_HEADER] = csrf_token
    assert await validate_csrf(redis, req) is True
