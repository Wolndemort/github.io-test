from datetime import date, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from auth.context import AuthContext
from auth import forecast_routes
from auth.forecast_routes import build_forecast_payload, forecast_access
from auth.forecast_routes import forecast_data


def request():
    return SimpleNamespace()


@pytest.mark.asyncio
async def test_forecast_access_is_read_only_for_staff_with_permission():
    actor = AuthContext(42, 7, "staff", "manager", frozenset({"forecast_view"}), "telegram")
    result = await forecast_access(request(), actor)
    assert result["ok"] is True
    assert result["club_id"] == 7
    assert result["read_only"] is True


@pytest.mark.asyncio
async def test_forecast_access_rejects_staff_without_permission():
    actor = AuthContext(42, 7, "staff", "cashier", frozenset(), "telegram")
    with pytest.raises(HTTPException) as error:
        await forecast_access(request(), actor)
    assert error.value.status_code == 403
    assert error.value.detail["code"] == "permission_denied"


@pytest.mark.asyncio
async def test_forecast_access_requires_auth_context():
    with pytest.raises(HTTPException) as error:
        await forecast_access(request(), None)
    assert error.value.status_code == 401


def test_forecast_json_contract_is_scoped_and_read_only(monkeypatch):
    club = SimpleNamespace(id=7, club_settings={})
    student = SimpleNamespace(id=11, name="Test", expire_date=datetime(2026, 9, 1), last_visit=None, discipline="boxing")
    monkeypatch.setattr(forecast_routes, "reporting_periods", lambda: {"now": datetime(2026, 8, 20), "local_now": datetime(2026, 8, 20)})
    monkeypatch.setattr(forecast_routes, "calculate_projected_renewal_revenue", lambda *args: {"students": [student], "discipline_counts": {"boxing": 1}})
    monkeypatch.setattr(forecast_routes, "build_expiry_series", lambda *args: {"series": [{"date": "2026-09-01", "count": 1}], "peak_count": 1})
    monkeypatch.setattr(forecast_routes, "build_revenue_series", lambda *args: {"series": [], "peak_amount": 0})
    monkeypatch.setattr(forecast_routes, "build_visit_series", lambda *args: {"series": [], "peak_count": 0})

    result = build_forecast_payload(
        club=club, students=[student], visits=[], payments=[], cart_orders=[], cash_entries=[],
        start=date(2026, 8, 1), finish=date(2026, 9, 1),
        revenue_start=date(2026, 8, 1), revenue_finish=date(2026, 9, 1),
        visits_start=date(2026, 8, 1), visits_finish=date(2026, 9, 1),
    )

    assert result["club_id"] == 7
    assert result["forecast"]["students"][0]["student_id"] == 11
    assert result["read_only"] is True
    assert "bot_token" not in result


class _EmptyResult:
    def scalars(self):
        return self

    def all(self):
        return []


class _ScopedSession:
    def __init__(self, club_id):
        self.club_id = club_id
        self.queries = []

    async def scalar(self, statement):
        self.queries.append(statement)
        return SimpleNamespace(id=self.club_id, club_settings={})

    async def execute(self, statement):
        self.queries.append(statement)
        assert self.club_id in statement.compile().params.values()
        return _EmptyResult()


@pytest.mark.asyncio
async def test_forecast_data_scopes_every_query_to_authenticated_club(monkeypatch):
    actor = AuthContext(42, 19, "staff", "manager", frozenset({"forecast_view"}), "web")
    session = _ScopedSession(actor.club_id)
    monkeypatch.setattr(forecast_routes, "reporting_periods", lambda: {"now": datetime(2026, 8, 20), "local_now": datetime(2026, 8, 20)})
    monkeypatch.setattr(forecast_routes, "build_forecast_payload", lambda **kwargs: {"club_id": kwargs["club"].id, "read_only": True})

    result = await forecast_data(request(), actor, session, None, None, None, None, None, None)

    assert result == {"club_id": 19, "read_only": True}
    assert len(session.queries) == 6
