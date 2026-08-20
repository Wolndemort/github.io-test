from types import SimpleNamespace
import pytest
from auth import forecast_routes
from auth.context import AuthContext

class Session:
    async def scalar(self, statement):
        assert 161 in statement.compile().params.values()
        return SimpleNamespace(id=161,name="Club Name",club_settings={"ui":{"club_name":"Public Club","theme":"monochrome","secret":"hidden"}})

@pytest.mark.asyncio
async def test_branding_is_allowlisted_and_scoped():
    actor=AuthContext(1,161,"staff","manager",frozenset(),"web")
    result=await forecast_routes.branding_data(SimpleNamespace(),actor,Session())
    assert result["club_id"]==161 and result["branding"]["club_name"]=="Public Club" and "secret" not in result["branding"]

@pytest.mark.asyncio
async def test_branding_page_uses_shared_settings_helper():
    response=await forecast_routes.web_branding_page(SimpleNamespace())
    assert "/api/v1/staff/settings/branding" in response.body.decode()
