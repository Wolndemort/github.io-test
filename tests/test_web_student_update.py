from types import SimpleNamespace
import pytest
from fastapi import HTTPException

from auth import forecast_routes
from auth.context import AuthContext


class Redis:
    def __init__(self): self.keys = set()
    async def get(self, key): return '{"csrf_token":"csrf"}'
    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.keys: return False
        self.keys.add(key); return True


class Request:
    app = SimpleNamespace(state=SimpleNamespace(redis_client=Redis()))
    cookies = {"speedycrm_web_session": "sid"}
    headers = {"x-csrf-token": "csrf"}
    async def json(self): return {"name": "Updated", "discipline": "boxing", "idempotency_key": "u-1"}


class Session:
    def __init__(self, student=None): self.student = student or SimpleNamespace(id=7, name="Old", discipline="boxing"); self.committed = False
    async def scalar(self, statement): return self.student
    async def commit(self): self.committed = True


class EmptySession(Session):
    def __init__(self): super().__init__(); self.student = None


@pytest.mark.asyncio
async def test_staff_can_update_student_inside_authenticated_club(monkeypatch):
    monkeypatch.setenv("WEB_STUDENT_MUTATIONS_ENABLED", "1")
    actor = AuthContext(1, 23, "staff", "manager", frozenset({"analytics_view"}), "web")
    session = Session()
    result = await forecast_routes.update_student_data(7, Request(), actor, session)
    assert result["ok"] and result["club_id"] == 23 and session.committed


@pytest.mark.asyncio
async def test_student_update_is_idempotent_and_feature_gated(monkeypatch):
    actor = AuthContext(1, 23, "owner", "owner", frozenset(), "web")
    monkeypatch.delenv("WEB_STUDENT_MUTATIONS_ENABLED", raising=False)
    with pytest.raises(HTTPException) as error:
        await forecast_routes.update_student_data(7, Request(), actor, Session())
    assert error.value.status_code == 404
    monkeypatch.setenv("WEB_STUDENT_MUTATIONS_ENABLED", "1")
    request = Request(); session = Session()
    await forecast_routes.update_student_data(7, request, actor, session)
    replay = await forecast_routes.update_student_data(7, request, actor, session)
    assert replay["idempotent_replay"] is True


@pytest.mark.asyncio
async def test_student_update_rejects_foreign_club_and_unsafe_fields(monkeypatch):
    monkeypatch.setenv("WEB_STUDENT_MUTATIONS_ENABLED", "1")
    actor = AuthContext(1, 23, "owner", "owner", frozenset(), "web")
    class ForeignRequest(Request):
        async def json(self): return {"name": "Updated", "idempotency_key": "foreign-1"}
    with pytest.raises(HTTPException) as error:
        await forecast_routes.update_student_data(7, ForeignRequest(), actor, EmptySession())
    assert error.value.status_code == 404

    class Unsafe(Request):
        async def json(self): return {"balance_lessons": 999, "idempotency_key": "unsafe"}
    with pytest.raises(HTTPException) as unsafe:
        await forecast_routes.update_student_data(7, Unsafe(), actor, Session())
    assert unsafe.value.status_code == 400
