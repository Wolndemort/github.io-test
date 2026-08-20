from types import SimpleNamespace
import pytest
from auth import forecast_routes
from auth.context import AuthContext

class Session:
    async def scalar(self,statement):
        assert 251 in statement.compile().params.values()
        return SimpleNamespace(id=251,name="Club",bot_token="token",club_settings={"disciplines":{"boxing":{"tariffs":[{"price":100}]}},"notifications":{"email_enabled":True},"ui":{"club_name":"Public"}})

@pytest.mark.asyncio
async def test_client_tariffs_notifications_and_club_are_scoped():
    actor=AuthContext(1,251,"client","client",frozenset(),"web")
    tariffs=await forecast_routes.client_tariffs_data(SimpleNamespace(),actor,Session()); notifications=await forecast_routes.client_notifications_data(SimpleNamespace(),actor,Session()); club=await forecast_routes.client_club_data(SimpleNamespace(),actor,Session())
    assert tariffs["tariffs"]["boxing"]==[{"price":100}]
    assert notifications["notifications"]["telegram_enabled"] is True
    assert club["club"]["name"]=="Public"

@pytest.mark.asyncio
async def test_client_batch_pages_use_shared_endpoints():
    actor=SimpleNamespace()
    for page,endpoint in ((forecast_routes.web_client_tariffs_page,"/api/v1/client/tariffs/data"),(forecast_routes.web_client_notifications_page,"/api/v1/client/notifications/data"),(forecast_routes.web_client_club_page,"/api/v1/client/club/data")):
        assert endpoint in (await page(actor)).body.decode()
