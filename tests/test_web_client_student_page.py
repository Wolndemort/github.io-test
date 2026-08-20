from types import SimpleNamespace
import pytest
from auth import forecast_routes
from fastapi import HTTPException

@pytest.mark.asyncio
async def test_student_create_page_uses_csrf_and_shared_api():
    response=await forecast_routes.web_client_student_create_page(SimpleNamespace())
    body=response.body.decode()
    assert "/api/v1/client/students" in body
    assert "X-CSRF-Token" in body
    assert "speedycrm_csrf_token" in body
    assert "/client/cabinet" in body

@pytest.mark.asyncio
async def test_student_create_page_requires_auth():
    with pytest.raises(HTTPException): await forecast_routes.web_client_student_create_page(None)
