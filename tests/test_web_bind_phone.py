from types import SimpleNamespace
import json
import pytest
from fastapi import HTTPException
from auth import forecast_routes
from auth.context import AuthContext

class Redis:
    def __init__(self): self.count = 0
    async def get(self, key): return json.dumps({"csrf_token": "csrf"})
    async def incr(self, key): self.count += 1; return self.count
    async def expire(self, key, ttl): pass
class Request:
    app = SimpleNamespace(state=SimpleNamespace(redis_client=Redis()))
    cookies = {"speedycrm_web_session": "sid"}
    headers = {"x-csrf-token": "csrf"}
    async def json(self): return {"phone": "+7 (900) 111-22-33"}
class SpoofedRequest(Request):
    async def json(self): return {"phone": "+7 (900) 111-22-33", "user_id": 999999, "club_id": 999999}
class Result:
    def scalars(self): return self
    def all(self): return [SimpleNamespace(id=9, club_id=41, parent_phone="+79001112233", parent_phone_secondary=None)]
class Session:
    def __init__(self): self.added=[]
    async def execute(self, statement): return Result()
    async def get(self, model, key, **kwargs): return None
    def add(self, value): self.added.append(value)
    async def flush(self): pass
    async def commit(self): pass

@pytest.mark.asyncio
async def test_bind_phone_requires_csrf_and_uses_session_identity(monkeypatch):
    monkeypatch.setenv("WEB_CLIENT_BIND_PHONE_ENABLED", "1")
    actor = AuthContext(801, 41, "client", "client", frozenset(), "web")
    result = await forecast_routes.client_bind_phone(Request(), actor, Session())
    assert result["club_id"] == 41
    assert result["student_ids"] == [9]

@pytest.mark.asyncio
async def test_bind_phone_ignores_spoofed_user_and_club_payload_fields(monkeypatch):
    monkeypatch.setenv("WEB_CLIENT_BIND_PHONE_ENABLED", "1")
    actor = AuthContext(801, 41, "client", "client", frozenset(), "web")
    result = await forecast_routes.client_bind_phone(SpoofedRequest(), actor, Session())
    assert result["club_id"] == 41
    assert result["student_ids"] == [9]

@pytest.mark.asyncio
async def test_bind_phone_rejects_invalid_csrf(monkeypatch):
    monkeypatch.setenv("WEB_CLIENT_BIND_PHONE_ENABLED", "1")
    class BadRequest(Request): headers = {}
    actor = AuthContext(801, 41, "client", "client", frozenset(), "web")
    with pytest.raises(HTTPException) as error:
        await forecast_routes.client_bind_phone(BadRequest(), actor, Session())
    assert error.value.status_code == 403

@pytest.mark.asyncio
async def test_bind_phone_rate_limits_repeated_attempts(monkeypatch):
    monkeypatch.setenv("WEB_CLIENT_BIND_PHONE_ENABLED", "1")
    actor = AuthContext(801, 41, "client", "client", frozenset(), "web")
    redis = Redis(); request = Request(); request.app.state.redis_client = redis
    for _ in range(3): await forecast_routes.client_bind_phone(request, actor, Session())
    with pytest.raises(HTTPException) as error:
        await forecast_routes.client_bind_phone(request, actor, Session())
    assert error.value.status_code == 429

@pytest.mark.asyncio
async def test_bind_phone_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("WEB_CLIENT_BIND_PHONE_ENABLED", raising=False)
    actor = AuthContext(801, 41, "client", "client", frozenset(), "web")
    with pytest.raises(HTTPException) as error:
        await forecast_routes.client_bind_phone(Request(), actor, Session())
    assert error.value.status_code == 404
