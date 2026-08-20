from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from auth import forecast_routes
from auth.context import AuthContext

class Result:
    def __init__(self, rows): self.rows = rows
    def scalars(self): return self
    def all(self): return self.rows

class Session:
    def __init__(self, rows): self.rows = rows
    async def execute(self, statement):
        assert 21 in statement.compile().params.values()
        rows, self.rows = self.rows, []
        return Result(rows)

@pytest.mark.asyncio
async def test_cash_data_is_scoped_and_calculates_balance():
    actor = AuthContext(1, 21, "staff", "manager", frozenset({"cash_view"}), "web")
    rows = [SimpleNamespace(entry_type="income", amount_kopecks=1000), SimpleNamespace(entry_type="expense", amount_kopecks=250)]
    result = await forecast_routes.cash_data(SimpleNamespace(), actor, Session(rows))
    assert result["balance_kopecks"] == 750
    assert result["read_only"] is True

@pytest.mark.asyncio
async def test_sales_data_is_scoped_and_calculates_total():
    actor = AuthContext(1, 21, "staff", "manager", frozenset({"analytics_view"}), "web")
    rows = [SimpleNamespace(amount_kopecks=1000)]
    result = await forecast_routes.sales_data(SimpleNamespace(), actor, Session(rows))
    assert result["sales_count"] == 1
    assert result["amount_kopecks"] == 1000

@pytest.mark.asyncio
async def test_cash_and_sales_permissions_are_enforced():
    actor = SimpleNamespace(actor_type="staff", permissions=set())
    with pytest.raises(HTTPException): await forecast_routes.cash_data(SimpleNamespace(), actor, Session([]))
    with pytest.raises(HTTPException): await forecast_routes.sales_data(SimpleNamespace(), actor, Session([]))
