from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from auth import forecast_routes
from auth.context import AuthContext


class Result:
    def scalars(self): return self
    def all(self): return [SimpleNamespace(id=3, name="A", discipline="boxing", parent_id=4)]


class Session:
    async def execute(self, statement):
        assert 12 in statement.compile().params.values()
        return Result()


@pytest.mark.asyncio
async def test_students_data_is_club_scoped_and_read_only():
    actor = AuthContext(1, 12, "staff", "manager", frozenset({"analytics_view"}), "web")
    result = await forecast_routes.students_data(SimpleNamespace(), actor, Session(), "Ann", 10, 20)
    assert result["club_id"] == 12
    assert result["students"] == [{"id": 3, "name": "A", "discipline": "boxing", "parent_id": 4}]
    assert result["read_only"] is True
    assert result["pagination"] == {"limit": 10, "offset": 20, "returned": 1, "query": "Ann"}


@pytest.mark.asyncio
async def test_students_page_rejects_without_permission():
    actor = SimpleNamespace(actor_type="staff", permissions=set())
    with pytest.raises(HTTPException) as error:
        await forecast_routes.web_students_page(actor)
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_students_page_exposes_shared_search_control():
    actor = SimpleNamespace(actor_type="staff", permissions={"analytics_view"})
    response = await forecast_routes.web_students_page(actor)
    body = response.body.decode()
    assert 'id="student-filter"' in body
    assert "/api/v1/staff/students/data?limit=50&q=" in body
