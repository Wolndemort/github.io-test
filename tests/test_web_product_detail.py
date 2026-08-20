from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from auth import forecast_routes
from auth.context import AuthContext

class Session:
    async def scalar(self, statement):
        params=statement.compile().params; assert 181 in params.values() and 6 in params.values()
        return SimpleNamespace(id=6,name="Water",category="drink",price_kopecks=100,stock=4,details="cold")

@pytest.mark.asyncio
async def test_product_detail_is_scoped_and_read_only():
    actor=AuthContext(1,181,"staff","manager",frozenset({"products_view"}),"web")
    result=await forecast_routes.product_detail_data(6,SimpleNamespace(),actor,Session())
    assert result["club_id"]==181 and result["product"]["name"]=="Water" and result["read_only"] is True

@pytest.mark.asyncio
async def test_product_detail_page_requires_permission():
    with pytest.raises(HTTPException): await forecast_routes.web_product_detail_page(6,SimpleNamespace(actor_type="staff",permissions=set()))
