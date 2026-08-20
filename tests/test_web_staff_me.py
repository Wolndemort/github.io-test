from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from auth import forecast_routes

@pytest.mark.asyncio
async def test_staff_me_page_uses_auth_me():
    response=await forecast_routes.web_staff_me_page(SimpleNamespace(actor_type="staff",permissions={"analytics_view"}))
    assert "/auth/me" in response.body.decode()

@pytest.mark.asyncio
async def test_staff_me_rejects_client_context():
    with pytest.raises(HTTPException): await forecast_routes.web_staff_me_page(SimpleNamespace(actor_type="client",permissions=set()))
