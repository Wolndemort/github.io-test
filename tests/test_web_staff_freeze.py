from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from auth import forecast_routes
from auth.context import AuthContext

class Result:
    def scalars(self): return self
    def all(self): return [SimpleNamespace(id=5,name="Frozen",discipline="boxing",frozen_at=None,frozen_days=7)]
class Session:
    async def execute(self, statement):
        assert 91 in statement.compile().params.values(); return Result()

@pytest.mark.asyncio
async def test_staff_freeze_is_scoped_and_read_only():
    actor=AuthContext(1,91,"staff","manager",frozenset({"analytics_view"}),"web")
    result=await forecast_routes.staff_freeze_data(SimpleNamespace(),actor,Session())
    assert result["club_id"]==91 and result["frozen"][0]["id"]==5 and result["read_only"] is True

@pytest.mark.asyncio
async def test_staff_freeze_requires_permission():
    with pytest.raises(HTTPException): await forecast_routes.web_staff_freeze_page(SimpleNamespace(actor_type="staff",permissions=set()))
