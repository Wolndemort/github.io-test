from types import SimpleNamespace
import pytest
from auth import forecast_routes
from auth.context import AuthContext

class Result:
    def scalars(self): return self
    def all(self): return [SimpleNamespace(id=3, name="Kid", discipline="boxing", expire_date=None, balance_lessons=5, is_frozen=False)]
class Session:
    async def execute(self, statement):
        assert 33 in statement.compile().params.values()
        assert 702 in statement.compile().params.values()
        return Result()

@pytest.mark.asyncio
async def test_client_subscriptions_scope_identity_and_club():
    actor = AuthContext(702, 33, "client", "client", frozenset(), "web")
    result = await forecast_routes.client_subscriptions_data(SimpleNamespace(), actor, Session())
    assert result["club_id"] == 33
    assert result["subscriptions"][0]["student_id"] == 3
    assert result["read_only"] is True

@pytest.mark.asyncio
async def test_client_subscriptions_page_uses_shared_endpoint():
    response = await forecast_routes.web_client_subscriptions_page(SimpleNamespace())
    assert "/api/v1/client/subscriptions/data" in response.body.decode()
