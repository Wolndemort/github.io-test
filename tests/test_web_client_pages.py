from types import SimpleNamespace
import pytest
from auth import forecast_routes
from auth.context import AuthContext

class Result:
    def __init__(self, rows): self.rows = rows
    def scalars(self): return self
    def all(self): return self.rows
class Session:
    def __init__(self): self.i=0
    async def execute(self, statement):
        assert 32 in statement.compile().params.values()
        self.i += 1
        return Result([SimpleNamespace(id=2, parent_id=701, club_id=32, student_id=2, visited_at=None, source="gate", name="Kid", is_frozen=False)])

@pytest.mark.asyncio
async def test_client_history_is_scoped_to_authenticated_user_and_club():
    actor = AuthContext(701, 32, "client", "client", frozenset(), "web")
    result = await forecast_routes.client_history_data(SimpleNamespace(), actor, Session(), 10)
    assert result["club_id"] == 32 and result["read_only"] is True

@pytest.mark.asyncio
async def test_client_freeze_is_read_only():
    actor = AuthContext(701, 32, "client", "client", frozenset(), "web")
    result = await forecast_routes.client_freeze_data(SimpleNamespace(), actor, Session())
    assert result["students"][0]["is_frozen"] is False
    assert result["read_only"] is True

@pytest.mark.asyncio
async def test_client_history_and_freeze_pages_use_shared_api():
    actor = SimpleNamespace()
    history = await forecast_routes.web_client_history_page(actor)
    freeze = await forecast_routes.web_client_freeze_page(actor)
    assert "/api/v1/client/history/data" in history.body.decode()
    assert "/api/v1/client/freeze/data" in freeze.body.decode()
