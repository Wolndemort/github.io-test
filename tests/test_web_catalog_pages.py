from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from auth import forecast_routes

@pytest.mark.asyncio
async def test_catalog_pages_use_shared_api_shell():
    cases = [(forecast_routes.web_products_page, "products_view", "/api/v1/staff/catalog/products"), (forecast_routes.web_discounts_page, "analytics_view", "/api/v1/staff/catalog/discounts"), (forecast_routes.web_tariffs_page, "tariffs_manage", "/api/v1/staff/catalog/tariffs")]
    for page, permission, endpoint in cases:
        response = await page(SimpleNamespace(actor_type="staff", permissions={permission}))
        assert endpoint in response.body.decode()

@pytest.mark.asyncio
async def test_catalog_pages_enforce_permissions():
    actor = SimpleNamespace(actor_type="staff", permissions=set())
    for page in (forecast_routes.web_products_page, forecast_routes.web_discounts_page, forecast_routes.web_tariffs_page):
        with pytest.raises(HTTPException) as error: await page(actor)
        assert error.value.status_code == 403
