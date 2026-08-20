import json
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from auth import forecast_routes
from auth.context import AuthContext

class Redis:
    def __init__(self): self.keys=set()
    async def get(self, key): return json.dumps({"csrf_token":"csrf"})
    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.keys: return False
        self.keys.add(key); return True
class Request:
    app=SimpleNamespace(state=SimpleNamespace(redis_client=Redis()))
    cookies={"speedycrm_web_session":"sid"}; headers={"x-csrf-token":"csrf"}
    async def json(self): return {"name":"Child", "discipline":"boxing", "idempotency_key":"student-1", "user_id":999, "club_id":999}
class Session:
    def __init__(self, duplicate=None): self.duplicate=duplicate; self.added=[]
    async def scalar(self, statement): return self.duplicate
    def add(self, value): self.added.append(value); value.id=77
    async def commit(self): pass

@pytest.mark.asyncio
async def test_create_student_uses_auth_identity_and_csrf(monkeypatch):
    monkeypatch.setenv("WEB_CLIENT_STUDENT_MUTATIONS_ENABLED", "1")
    actor=AuthContext(901, 51, "client", "client", frozenset(), "web")
    result=await forecast_routes.client_create_student(Request(), actor, Session())
    assert result["club_id"]==51 and result["student_id"]==77

@pytest.mark.asyncio
async def test_create_student_is_idempotent_on_retry(monkeypatch):
    monkeypatch.setenv("WEB_CLIENT_STUDENT_MUTATIONS_ENABLED", "1")
    actor=AuthContext(901, 51, "client", "client", frozenset(), "web"); req=Request(); session=Session()
    await forecast_routes.client_create_student(req, actor, session)
    replay=await forecast_routes.client_create_student(req, actor, session)
    assert replay["idempotent_replay"] is True

@pytest.mark.asyncio
async def test_create_student_rejects_duplicate_and_invalid_csrf(monkeypatch):
    monkeypatch.setenv("WEB_CLIENT_STUDENT_MUTATIONS_ENABLED", "1")
    actor=AuthContext(901, 51, "client", "client", frozenset(), "web")
    class DuplicateRequest(Request):
        async def json(self): return {"name":"Child", "discipline":"boxing", "idempotency_key":"student-duplicate"}
    with pytest.raises(HTTPException) as duplicate:
        await forecast_routes.client_create_student(DuplicateRequest(), actor, Session(SimpleNamespace(id=1)))
    assert duplicate.value.status_code==409
    class Bad(Request): headers={}
    with pytest.raises(HTTPException) as csrf:
        await forecast_routes.client_create_student(Bad(), actor, Session())
    assert csrf.value.status_code==403

@pytest.mark.asyncio
async def test_create_student_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("WEB_CLIENT_STUDENT_MUTATIONS_ENABLED", raising=False)
    actor=AuthContext(901, 51, "client", "client", frozenset(), "web")
    with pytest.raises(HTTPException) as error:
        await forecast_routes.client_create_student(Request(), actor, Session())
    assert error.value.status_code == 404
    assert error.value.detail["code"] == "feature_disabled"
