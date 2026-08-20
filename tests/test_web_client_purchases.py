from types import SimpleNamespace
import pytest
from auth import forecast_routes
from auth.context import AuthContext

class Result:
    def scalars(self): return self
    def all(self): return [SimpleNamespace(id="order-1",amount_kopecks=500,created_at=None,discount_name=None)]
class Session:
    async def execute(self, statement):
        params=statement.compile().params
        assert 141 in params.values() and 1101 in params.values(); return Result()

@pytest.mark.asyncio
async def test_client_purchases_scope_user_and_club():
    actor=AuthContext(1101,141,"client","client",frozenset(),"web")
    result=await forecast_routes.client_purchases_data(SimpleNamespace(),actor,Session(),20)
    assert result["club_id"]==141 and result["purchases"][0]["order_id"]=="order-1" and result["read_only"] is True

@pytest.mark.asyncio
async def test_client_purchases_page_uses_endpoint():
    response=await forecast_routes.web_client_purchases_page(SimpleNamespace())
    assert "/api/v1/client/purchases/data" in response.body.decode()
