from types import SimpleNamespace
import pytest
from auth import forecast_routes
from auth.context import AuthContext

class Session:
    async def scalar(self, statement):
        assert 221 in statement.compile().params.values()
        return SimpleNamespace(id=221,club_settings={"disciplines":{"boxing":{"schedule":{"mon":[]}}},"secret":"hidden"})

@pytest.mark.asyncio
async def test_client_schedule_is_club_scoped_and_allowlisted():
    actor=AuthContext(1,221,"client","client",frozenset(),"web")
    result=await forecast_routes.client_schedule_data(SimpleNamespace(),actor,Session())
    assert result["club_id"]==221 and "boxing" in result["schedule"] and "secret" not in result

@pytest.mark.asyncio
async def test_client_schedule_page_uses_endpoint():
    response=await forecast_routes.web_client_schedule_page(SimpleNamespace())
    assert "/api/v1/client/schedule/data" in response.body.decode()
