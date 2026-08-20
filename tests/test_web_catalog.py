from types import SimpleNamespace
import pytest
from auth import forecast_routes
from auth.context import AuthContext

class Result:
    def __init__(self, rows): self.rows = rows
    def scalars(self): return self
    def all(self): return self.rows
class Session:
    def __init__(self, rows): self.rows = rows
    async def execute(self, statement):
        assert 24 in statement.compile().params.values(); return Result(self.rows)

@pytest.mark.asyncio
async def test_products_and_discounts_are_club_scoped():
    actor = AuthContext(1, 24, "staff", "manager", frozenset({"products_view", "analytics_view"}), "web")
    product = SimpleNamespace(id=1, name="Water", category="drink", price_kopecks=100, stock=2)
    discount = SimpleNamespace(id=2, name="Family", kind="percent", value=10, scope="all", priority=1)
    products = await forecast_routes.products_data(SimpleNamespace(), actor, Session([product]))
    discounts = await forecast_routes.discounts_data(SimpleNamespace(), actor, Session([discount]))
    assert products["club_id"] == discounts["club_id"] == 24
    assert products["read_only"] and discounts["read_only"]

@pytest.mark.asyncio
async def test_tariffs_are_scoped_to_club_settings():
    actor = AuthContext(1, 24, "staff", "manager", frozenset({"tariffs_manage"}), "web")
    session = SimpleNamespace(scalar=lambda statement: None)
    async def scalar(statement):
        assert 24 in statement.compile().params.values()
        return SimpleNamespace(id=24, club_settings={"disciplines": {"boxing": {"tariffs": [{"price": 100}]}}})
    session.scalar = scalar
    result = await forecast_routes.tariffs_data(SimpleNamespace(), actor, session)
    assert result["tariffs"]["boxing"] == [{"price": 100}]
