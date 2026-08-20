from types import SimpleNamespace
import pytest
from auth import forecast_routes
from auth.context import AuthContext

class Result:
    def scalars(self): return self
    def all(self): return [SimpleNamespace(id=4,name="Family",kind="percent",value=10,scope="subscriptions")]
class Session:
    async def scalar(self, statement):
        assert 131 in statement.compile().params.values(); return SimpleNamespace(id=8,club_id=131)
    async def execute(self, statement):
        params=statement.compile().params; assert 131 in params.values() and 8 in params.values(); return Result()

@pytest.mark.asyncio
async def test_student_discounts_are_scoped_to_student_and_club():
    actor=AuthContext(1,131,"staff","manager",frozenset({"analytics_view"}),"web")
    result=await forecast_routes.student_discounts_data(8,SimpleNamespace(),actor,Session())
    assert result["club_id"]==131 and result["discounts"][0]["scope"]=="subscriptions" and result["read_only"] is True

@pytest.mark.asyncio
async def test_student_discounts_page_requires_permission():
    from fastapi import HTTPException
    with pytest.raises(HTTPException): await forecast_routes.web_student_discounts_page(8,SimpleNamespace(actor_type="staff",permissions=set()))
