from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from auth import forecast_routes
from auth.context import AuthContext

class Result:
    def __init__(self, rows): self.rows=rows
    def scalars(self): return self
    def all(self): return self.rows
class Session:
    def __init__(self): self.i=0
    async def scalar(self, statement):
        assert 111 in statement.compile().params.values(); return SimpleNamespace(id=9,club_id=111)
    async def execute(self, statement):
        assert 111 in statement.compile().params.values() and 9 in statement.compile().params.values()
        return Result([SimpleNamespace(visited_at=None,source="qr")])

@pytest.mark.asyncio
async def test_student_visits_are_double_scoped():
    actor=AuthContext(1,111,"staff","manager",frozenset({"analytics_view"}),"web")
    result=await forecast_routes.student_visits_data(9,SimpleNamespace(),actor,Session(),20)
    assert result["club_id"]==111 and result["student_id"]==9 and result["read_only"] is True

@pytest.mark.asyncio
async def test_student_visits_page_requires_permission():
    with pytest.raises(HTTPException): await forecast_routes.web_student_visits_page(9,SimpleNamespace(actor_type="staff",permissions=set()))
