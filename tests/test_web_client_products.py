from types import SimpleNamespace
import pytest
from auth import forecast_routes
from auth.context import AuthContext

class Result:
    def scalars(self): return self
    def all(self): return [SimpleNamespace(id=1,name="Water",category="drink",price_kopecks=100,details="cold")]
class Session:
    async def execute(self, statement):
        assert 231 in statement.compile().params.values(); return Result()

@pytest.mark.asyncio
async def test_client_products_are_active_and_club_scoped():
    actor=AuthContext(1,231,"client","client",frozenset(),"web")
    result=await forecast_routes.client_products_data(SimpleNamespace(),actor,Session())
    assert result["club_id"]==231 and result["products"][0]["name"]=="Water" and result["read_only"] is True

@pytest.mark.asyncio
async def test_client_products_page_uses_endpoint():
    response=await forecast_routes.web_client_products_page(SimpleNamespace())
    assert "/api/v1/client/products/data" in response.body.decode()
