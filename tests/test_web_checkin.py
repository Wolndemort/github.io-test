from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from auth import forecast_routes
from auth.context import AuthContext

class Result:
    def scalars(self): return self
    def all(self): return [SimpleNamespace(student_id=4, visited_at=None, source="qr")]
class Session:
    async def execute(self, statement):
        assert 71 in statement.compile().params.values(); return Result()

@pytest.mark.asyncio
async def test_checkin_data_is_club_scoped_and_read_only():
    actor=AuthContext(1,71,"staff","manager",frozenset({"qr_checkin"}),"web")
    result=await forecast_routes.checkin_data(SimpleNamespace(),actor,Session(),20)
    assert result["club_id"]==71 and result["read_only"] is True
    assert result["visits"][0]["student_id"]==4

@pytest.mark.asyncio
async def test_checkin_requires_qr_permission():
    with pytest.raises(HTTPException): await forecast_routes.web_checkin_page(SimpleNamespace(actor_type="staff",permissions=set()))
