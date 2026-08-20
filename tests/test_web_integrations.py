from types import SimpleNamespace
import pytest
from auth import forecast_routes
from auth.context import AuthContext

class Session:
    async def scalar(self, statement):
        assert 171 in statement.compile().params.values()
        return SimpleNamespace(id=171,bot_token="token",club_settings={"payments":{"yookassa_shop_id":"shop","yookassa_secret_key":"secret"},"notifications":{"email_enabled":True,"push_enabled":False}})

@pytest.mark.asyncio
async def test_integrations_return_statuses_not_secrets():
    actor=AuthContext(1,171,"staff","manager",frozenset(),"web")
    result=await forecast_routes.integrations_data(SimpleNamespace(),actor,Session())
    assert result["integrations"]=={"telegram":True,"yookassa_configured":True,"email_enabled":True,"push_enabled":False}
    assert "secret" not in str(result)

@pytest.mark.asyncio
async def test_integrations_page_uses_shared_settings_helper():
    response=await forecast_routes.web_integrations_page(SimpleNamespace())
    assert "/api/v1/staff/settings/integrations" in response.body.decode()
