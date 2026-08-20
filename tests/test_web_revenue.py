from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from auth import forecast_routes
from auth.context import AuthContext


class Result:
    def scalars(self):
        return self

    def all(self):
        return []


class Session:
    def __init__(self, club_id):
        self.club_id = club_id
        self.queries = []

    async def execute(self, statement):
        self.queries.append(statement)
        assert self.club_id in statement.compile().params.values()
        return Result()


@pytest.mark.asyncio
async def test_revenue_data_requires_analytics_permission():
    actor = AuthContext(1, 8, "staff", "cashier", frozenset(), "web")
    with pytest.raises(HTTPException) as error:
        await forecast_routes.revenue_data(SimpleNamespace(), actor, Session(8))
    assert error.value.status_code == 403
    assert error.value.detail["code"] == "permission_denied"


@pytest.mark.asyncio
async def test_revenue_data_is_club_scoped_and_read_only(monkeypatch):
    actor = AuthContext(1, 8, "staff", "manager", frozenset({"analytics_view"}), "web")
    monkeypatch.setattr(forecast_routes, "calculate_revenue_periods", lambda rows: {"all": 12345})
    session = Session(8)
    result = await forecast_routes.revenue_data(SimpleNamespace(), actor, session)
    assert result == {"club_id": 8, "totals": {"all": 12345}, "read_only": True}
    assert len(session.queries) == 2
