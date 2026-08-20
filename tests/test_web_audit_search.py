from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from auth import forecast_routes

@pytest.mark.asyncio
async def test_audit_search_page_contains_filters_and_api():
    response=await forecast_routes.web_audit_search_page(SimpleNamespace(actor_type="staff",permissions={"analytics_view"}))
    body=response.body.decode()
    assert 'name="event"' in body and 'name="actor_role"' in body
    assert "/api/v1/staff/audit/data" in body

@pytest.mark.asyncio
async def test_audit_search_requires_permission():
    with pytest.raises(HTTPException): await forecast_routes.web_audit_search_page(SimpleNamespace(actor_type="staff",permissions=set()))
