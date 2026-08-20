from types import SimpleNamespace
import pytest
from auth import forecast_routes
from auth.context import AuthContext

class Result:
    def __init__(self,rows): self.rows=rows
    def scalars(self): return self
    def all(self): return self.rows
class Session:
    def __init__(self): self.i=0
    async def execute(self,statement):
        assert 261 in statement.compile().params.values(); self.i+=1
        return Result([SimpleNamespace(id=1,expire_date=None,balance_lessons=1,is_frozen=False,amount_kopecks=100)])

@pytest.mark.asyncio
async def test_client_summary_batch_is_scoped_and_read_only():
    actor=AuthContext(1301,261,"client","client",frozenset(),"web")
    attendance=await forecast_routes.client_attendance_summary(SimpleNamespace(),actor,Session()); subscriptions=await forecast_routes.client_subscription_summary(SimpleNamespace(),actor,Session()); purchases=await forecast_routes.client_purchase_summary(SimpleNamespace(),actor,Session())
    assert attendance["club_id"]==subscriptions["club_id"]==purchases["club_id"]==261
    assert attendance["read_only"] and subscriptions["read_only"] and purchases["read_only"]

@pytest.mark.asyncio
async def test_client_summary_pages_use_endpoints():
    actor=SimpleNamespace()
    for page,endpoint in ((forecast_routes.web_client_attendance_summary_page,"/api/v1/client/summary/attendance"),(forecast_routes.web_client_subscription_summary_page,"/api/v1/client/summary/subscriptions"),(forecast_routes.web_client_purchase_summary_page,"/api/v1/client/summary/purchases")):
        assert endpoint in (await page(actor)).body.decode()
