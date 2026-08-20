from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from auth import forecast_routes

@pytest.mark.asyncio
async def test_cash_page_uses_shared_shell_and_permission():
    response = await forecast_routes.web_cash_page(SimpleNamespace(actor_type="staff", permissions={"cash_view"}))
    assert response.status_code == 200
    assert "/api/v1/staff/cash/data" in response.body.decode()

@pytest.mark.asyncio
async def test_sales_page_uses_shared_shell_and_permission():
    response = await forecast_routes.web_sales_page(SimpleNamespace(actor_type="staff", permissions={"analytics_view"}))
    assert response.status_code == 200
    assert "/api/v1/staff/sales/data" in response.body.decode()

@pytest.mark.asyncio
async def test_cash_and_sales_pages_reject_missing_permissions():
    actor = SimpleNamespace(actor_type="staff", permissions=set())
    with pytest.raises(HTTPException): await forecast_routes.web_cash_page(actor)
    with pytest.raises(HTTPException): await forecast_routes.web_sales_page(actor)
