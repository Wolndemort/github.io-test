from types import SimpleNamespace
import pytest
from auth import forecast_routes
from auth.context import AuthContext

@pytest.mark.asyncio
async def test_client_me_returns_auth_context_without_sensitive_data():
    actor=AuthContext(901, 51, "client", "client", frozenset(), "web")
    result=await forecast_routes.client_me(SimpleNamespace(), actor)
    assert result == {"user_id":901,"club_id":51,"actor_type":"client","auth_source":"web","read_only":True}

@pytest.mark.asyncio
async def test_client_me_page_uses_endpoint():
    response=await forecast_routes.web_client_me_page(SimpleNamespace())
    assert "/api/v1/client/me" in response.body.decode()
