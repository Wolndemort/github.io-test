from types import SimpleNamespace
import pytest
from auth import forecast_routes
from auth.context import AuthContext

class Session:
    async def scalar(self, statement):
        assert 151 in statement.compile().params.values()
        return SimpleNamespace(id=151, club_settings={"limits":{"session_timeout_minutes":150,"max_upload_mb":20,"secret_key":"hidden"}})

@pytest.mark.asyncio
async def test_limits_are_allowlisted_and_club_scoped():
    actor=AuthContext(1,151,"staff","manager",frozenset(),"web")
    result=await forecast_routes.limits_data(SimpleNamespace(),actor,Session())
    assert result["club_id"]==151 and result["limits"]["max_upload_mb"]==20 and "secret_key" not in result["limits"]

@pytest.mark.asyncio
async def test_limits_page_uses_shared_settings_helper():
    response=await forecast_routes.web_limits_page(SimpleNamespace())
    assert "/api/v1/staff/settings/limits" in response.body.decode()
