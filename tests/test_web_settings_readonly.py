from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from auth import forecast_routes
from auth.context import AuthContext

class Session:
    async def scalar(self, statement):
        assert 61 in statement.compile().params.values()
        return SimpleNamespace(id=61, club_settings={"legal":{"provider_name":"Club"},"camera":{"enabled":True,"name":"cam"},"features":{"freeze":True}})

@pytest.mark.asyncio
async def test_settings_are_scoped_and_redacted():
    actor=AuthContext(1,61,"staff","manager",frozenset({"analytics_view","qr_checkin"}),"web")
    legal=await forecast_routes.legal_data(SimpleNamespace(),actor,Session()); camera=await forecast_routes.camera_data(SimpleNamespace(),actor,Session()); features=await forecast_routes.features_data(SimpleNamespace(),actor,Session())
    assert legal["legal"]["provider_name"]=="Club" and camera["camera"]["enabled"] is True and features["features"]["freeze"] is True

@pytest.mark.asyncio
async def test_camera_requires_permission():
    with pytest.raises(HTTPException): await forecast_routes.camera_data(SimpleNamespace(),SimpleNamespace(actor_type="staff",permissions=set()),Session())
