from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from auth import forecast_routes

@pytest.mark.asyncio
async def test_sale_detail_page_wires_shared_api():
    response=await forecast_routes.web_sale_detail_page("ord-1",SimpleNamespace(actor_type="staff",permissions={"analytics_view"}))
    assert "/api/v1/staff/sales/ord-1" in response.body.decode()

@pytest.mark.asyncio
async def test_sale_detail_page_requires_permission():
    with pytest.raises(HTTPException): await forecast_routes.web_sale_detail_page("ord-1",SimpleNamespace(actor_type="staff",permissions=set()))
