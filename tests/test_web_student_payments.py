from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from auth import forecast_routes
from auth.context import AuthContext

class Result:
    def scalars(self): return self
    def all(self): return [SimpleNamespace(amount_kopecks=1000,created_at=None,type="CASH")]
class Session:
    def __init__(self): self.calls=0
    async def scalar(self, statement):
        assert 121 in statement.compile().params.values(); return SimpleNamespace(id=7,club_id=121)
    async def execute(self, statement):
        params=statement.compile().params
        assert 121 in params.values() and 7 in params.values(); return Result()

@pytest.mark.asyncio
async def test_student_payments_are_scoped_and_confirmed_only():
    actor=AuthContext(1,121,"staff","manager",frozenset({"analytics_view"}),"web")
    result=await forecast_routes.student_payments_data(7,SimpleNamespace(),actor,Session(),20)
    assert result["club_id"]==121 and result["payments"][0]["amount_kopecks"]==1000 and result["read_only"] is True

@pytest.mark.asyncio
async def test_student_payments_page_requires_permission():
    with pytest.raises(HTTPException): await forecast_routes.web_student_payments_page(7,SimpleNamespace(actor_type="staff",permissions=set()))
