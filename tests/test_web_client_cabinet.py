from types import SimpleNamespace
import pytest
from auth import forecast_routes
from auth.context import AuthContext

class Result:
    def scalars(self): return self
    def all(self): return [SimpleNamespace(id=1, name="Child", discipline="boxing", expire_date=None, balance_lessons=4)]
class Session:
    async def execute(self, statement):
        params = statement.compile().params
        assert 31 in params.values()
        assert 501 in params.values()
        return Result()

@pytest.mark.asyncio
async def test_client_cabinet_scopes_by_authenticated_club_and_user():
    actor = AuthContext(501, 31, "client", "client", frozenset(), "web")
    result = await forecast_routes.client_cabinet_data(SimpleNamespace(), actor, Session())
    assert result["club_id"] == 31
    assert result["user_id"] == 501
    assert result["students"][0]["name"] == "Child"
    assert result["read_only"] is True

@pytest.mark.asyncio
async def test_client_cabinet_page_requires_authentication():
    from fastapi import HTTPException
    with pytest.raises(HTTPException): await forecast_routes.web_client_cabinet_page(None)
