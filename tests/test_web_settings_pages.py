from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from auth import forecast_routes

@pytest.mark.asyncio
async def test_settings_pages_use_shared_endpoints():
    cases=[(forecast_routes.web_legal_page,"analytics_view","/api/v1/staff/settings/legal"),(forecast_routes.web_camera_page,"qr_checkin","/api/v1/staff/settings/camera"),(forecast_routes.web_features_page,None,"/api/v1/staff/settings/features")]
    for page, permission, endpoint in cases:
        actor=SimpleNamespace(actor_type="staff",permissions={permission} if permission else set())
        response=await page(actor)
        assert endpoint in response.body.decode()

@pytest.mark.asyncio
async def test_settings_pages_enforce_restricted_permissions():
    with pytest.raises(HTTPException): await forecast_routes.web_legal_page(SimpleNamespace(actor_type="staff",permissions=set()))
    with pytest.raises(HTTPException): await forecast_routes.web_camera_page(SimpleNamespace(actor_type="staff",permissions=set()))
