from types import SimpleNamespace
import pytest
from auth import forecast_routes
from auth.context import AuthContext

class Session:
    async def scalar(self, statement):
        assert 81 in statement.compile().params.values()
        return SimpleNamespace(id=81, club_settings={"legal":{"provider_name":"Club","legal_address":"Address","inn":"hidden"}})

@pytest.mark.asyncio
async def test_client_legal_is_club_scoped_and_redacted():
    actor=AuthContext(1001,81,"client","client",frozenset(),"web")
    result=await forecast_routes.client_legal_data(SimpleNamespace(),actor,Session())
    assert result["club_id"]==81 and result["legal"]["provider_name"]=="Club"
    assert "inn" not in result["legal"]

@pytest.mark.asyncio
async def test_client_legal_page_uses_endpoint():
    response=await forecast_routes.web_client_legal_page(SimpleNamespace())
    assert "/api/v1/client/legal/data" in response.body.decode()
