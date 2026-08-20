from types import SimpleNamespace
import pytest
from auth import forecast_routes
from auth.context import AuthContext

class Result:
    def scalars(self): return self
    def all(self): return [SimpleNamespace(id=1,name="Family",kind="percent",value=10,scope="all")]
class Session:
    async def execute(self, statement):
        params=statement.compile().params; assert 241 in params.values() and 1201 in params.values(); return Result()

@pytest.mark.asyncio
async def test_client_discounts_are_assigned_to_user_and_club():
    actor=AuthContext(1201,241,"client","client",frozenset(),"web")
    result=await forecast_routes.client_discounts_data(SimpleNamespace(),actor,Session())
    assert result["club_id"]==241 and result["discounts"][0]["scope"]=="all" and result["read_only"] is True

@pytest.mark.asyncio
async def test_client_discounts_page_uses_endpoint():
    response=await forecast_routes.web_client_discounts_page(SimpleNamespace())
    assert "/api/v1/client/discounts/data" in response.body.decode()
