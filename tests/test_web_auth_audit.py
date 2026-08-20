import pytest
from types import SimpleNamespace
from auth import routes

@pytest.mark.asyncio
async def test_auth_logout_emits_safe_audit_event(monkeypatch):
    events=[]
    monkeypatch.setattr(routes, "audit_event", lambda event, **fields: events.append((event, fields)))
    monkeypatch.setattr(routes, "validate_csrf", lambda redis, request: _valid())
    redis=SimpleNamespace(delete=lambda key: None)
    async def delete(key): return None
    redis.delete=delete
    request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis_client=redis)),cookies={},headers={})
    response=await routes.auth_logout(request)
    assert response.status_code==200
    assert events[0][0]=="web_auth_logout"
    assert "init_data" not in str(events)

async def _valid():
    return True
