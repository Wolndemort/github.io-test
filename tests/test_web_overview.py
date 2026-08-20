from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from auth import forecast_routes
from auth.context import AuthContext


class Result:
    def __init__(self, rows): self.rows = rows
    def scalars(self): return self
    def all(self): return self.rows


class Session:
    def __init__(self): self.index = 0
    async def execute(self, statement):
        assert 15 in statement.compile().params.values()
        self.index += 1
        return Result([])


@pytest.mark.asyncio
async def test_overview_is_club_scoped_and_read_only(monkeypatch):
    actor = AuthContext(1, 15, "staff", "manager", frozenset({"analytics_view"}), "web")
    monkeypatch.setattr(forecast_routes, "calculate_admin_dashboard", lambda students, visit_logs: {"total_athletes": 0, "total_parents": 0, "active_now_count": 0})
    result = await forecast_routes.overview_data(SimpleNamespace(), actor, Session())
    assert result == {"club_id": 15, "metrics": {"total_athletes": 0, "total_parents": 0, "active_now_count": 0}, "read_only": True}


@pytest.mark.asyncio
async def test_overview_rejects_without_analytics_permission():
    actor = SimpleNamespace(actor_type="staff", permissions=set())
    with pytest.raises(HTTPException) as error:
        await forecast_routes.web_overview_page(actor)
    assert error.value.status_code == 403
