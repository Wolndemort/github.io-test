import pytest
from types import SimpleNamespace
from fastapi import HTTPException
from auth.routes import auth_login
from auth.forecast_routes import web_staff_entry

@pytest.mark.asyncio
async def test_auth_login_describes_one_time_exchange_only():
    result=await auth_login()
    assert result["method"]=="telegram_exchange"
    assert "/auth/telegram/exchange" in result["exchange_endpoint"]

@pytest.mark.asyncio
async def test_staff_entry_requires_staff_context():
    response=await web_staff_entry(SimpleNamespace(actor_type="staff",permissions=set()))
    assert "/staff/overview" in response.body.decode()
    with pytest.raises(HTTPException): await web_staff_entry(SimpleNamespace(actor_type="client",permissions=set()))
