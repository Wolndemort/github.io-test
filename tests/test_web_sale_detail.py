from types import SimpleNamespace
import pytest
from auth import forecast_routes
from auth.context import AuthContext

class Session:
    async def scalar(self, statement):
        params=statement.compile().params; assert 201 in params.values() and "ord-1" in params.values()
        return SimpleNamespace(id="ord-1",club_id=201,status="CONFIRMED",student_id=4,amount_kopecks=1000,created_at=None,type="CASH")

@pytest.mark.asyncio
async def test_sale_detail_is_club_scoped_and_confirmed():
    actor=AuthContext(1,201,"staff","manager",frozenset({"analytics_view"}),"web")
    result=await forecast_routes.sale_detail_data("ord-1",SimpleNamespace(),actor,Session())
    assert result["club_id"]==201 and result["sale"]["amount_kopecks"]==1000 and result["read_only"] is True

@pytest.mark.asyncio
async def test_sale_detail_requires_permission():
    from fastapi import HTTPException
    with pytest.raises(HTTPException): await forecast_routes.sale_detail_data("ord-1",SimpleNamespace(),SimpleNamespace(actor_type="staff",permissions=set()),Session())
