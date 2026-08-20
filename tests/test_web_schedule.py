from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from auth import forecast_routes
from auth.context import AuthContext

class Session:
    async def scalar(self, statement):
        assert 23 in statement.compile().params.values()
        return SimpleNamespace(id=23, club_settings={"disciplines": {"boxing": {"schedule": {"mon": [{"time": "10:00"}]}}}})

class Redis:
    def __init__(self): self.keys=set()
    async def get(self, key): return '{"csrf_token":"csrf"}'
    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.keys: return False
        self.keys.add(key); return True
class Request:
    app = SimpleNamespace(state=SimpleNamespace(redis_client=Redis()))
    cookies = {"speedycrm_web_session": "sid"}
    headers = {"x-csrf-token": "csrf"}
    async def json(self): return {"idempotency_key":"key-1", "discipline":"boxing", "day":"mon", "lessons":[{"time":"10:00"}]}
class UpdateSession(Session):
    def __init__(self): self.club=SimpleNamespace(id=23, club_settings={"disciplines":{"boxing":{"schedule":{}}}}); self.committed=False
    async def scalar(self, statement): return self.club
    async def commit(self): self.committed=True

@pytest.mark.asyncio
async def test_schedule_reads_only_authenticated_club_settings():
    actor = AuthContext(1, 23, "staff", "manager", frozenset({"schedule_view"}), "web")
    result = await forecast_routes.schedule_data(SimpleNamespace(), actor, Session())
    assert result["club_id"] == 23
    assert result["schedule"]["boxing"]["mon"][0]["time"] == "10:00"
    assert result["read_only"] is True

@pytest.mark.asyncio
async def test_schedule_requires_schedule_permission():
    actor = SimpleNamespace(actor_type="staff", permissions=set())
    with pytest.raises(HTTPException): await forecast_routes.web_schedule_page(actor)

@pytest.mark.asyncio
async def test_schedule_update_requires_permission_and_csrf(monkeypatch):
    monkeypatch.setenv("WEB_SCHEDULE_MUTATIONS_ENABLED", "1")
    actor = AuthContext(1, 23, "staff", "manager", frozenset({"schedule_edit"}), "web")
    result = await forecast_routes.update_schedule(Request(), actor, UpdateSession())
    assert result["ok"] is True and result["lesson_count"] == 1

@pytest.mark.asyncio
async def test_schedule_update_is_idempotent_and_rejects_bad_day(monkeypatch):
    monkeypatch.setenv("WEB_SCHEDULE_MUTATIONS_ENABLED", "1")
    actor = AuthContext(1, 23, "staff", "manager", frozenset({"schedule_edit"}), "web")
    request = Request(); session = UpdateSession()
    await forecast_routes.update_schedule(request, actor, session)
    replay = await forecast_routes.update_schedule(request, actor, session)
    assert replay["idempotent_replay"] is True
    class Bad(Request):
        async def json(self): return {"idempotency_key":"key-2", "discipline":"boxing", "day":"bad", "lessons":[]}
    with pytest.raises(HTTPException) as error: await forecast_routes.update_schedule(Bad(), actor, UpdateSession())
    assert error.value.status_code == 400

@pytest.mark.asyncio
async def test_schedule_update_rejects_malformed_lesson():
    monkeypatch = pytest.MonkeyPatch(); monkeypatch.setenv("WEB_SCHEDULE_MUTATIONS_ENABLED", "1")
    actor = AuthContext(1, 23, "staff", "manager", frozenset({"schedule_edit"}), "web")
    class BadLesson(Request):
        async def json(self): return {"idempotency_key":"key-3", "discipline":"boxing", "day":"mon", "lessons":[{"time":"25:99", "duration": 5}]}
    with pytest.raises(HTTPException) as error:
        await forecast_routes.update_schedule(BadLesson(), actor, UpdateSession())
    assert error.value.status_code == 400
    assert error.value.detail["code"] == "invalid_lesson_time"
    monkeypatch.undo()

@pytest.mark.asyncio
async def test_schedule_update_rejects_unapproved_lesson_fields():
    monkeypatch = pytest.MonkeyPatch(); monkeypatch.setenv("WEB_SCHEDULE_MUTATIONS_ENABLED", "1")
    actor = AuthContext(1, 23, "staff", "manager", frozenset({"schedule_edit"}), "web")
    class ExtraField(Request):
        async def json(self): return {"idempotency_key":"key-4", "discipline":"boxing", "day":"mon", "lessons":[{"time":"10:00", "secret":"no"}]}
    with pytest.raises(HTTPException) as error:
        await forecast_routes.update_schedule(ExtraField(), actor, UpdateSession())
    assert error.value.detail["code"] == "invalid_lesson_fields"
    monkeypatch.undo()

@pytest.mark.asyncio
async def test_schedule_mutation_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("WEB_SCHEDULE_MUTATIONS_ENABLED", raising=False)
    actor = AuthContext(1, 23, "staff", "manager", frozenset({"schedule_edit"}), "web")
    with pytest.raises(HTTPException) as error:
        await forecast_routes.update_schedule(Request(), actor, UpdateSession())
    assert error.value.status_code == 404
    assert error.value.detail["code"] == "feature_disabled"
