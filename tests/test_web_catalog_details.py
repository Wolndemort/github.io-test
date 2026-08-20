from types import SimpleNamespace
import pytest
from auth import forecast_routes
from auth.context import AuthContext

class Session:
    def __init__(self, club): self.club=club
    async def scalar(self, statement): return self.club

@pytest.mark.asyncio
async def test_discount_detail_is_scoped():
    actor=AuthContext(1,191,"staff","manager",frozenset({"analytics_view"}),"web")
    result=await forecast_routes.discount_detail_data(2,SimpleNamespace(),actor,Session(SimpleNamespace(id=2,club_id=191,is_active=True,name="Family",kind="percent",value=10,scope="all",priority=1,comment=None)))
    assert result["club_id"]==191 and result["read_only"] is True

@pytest.mark.asyncio
async def test_tariff_detail_is_scoped_to_discipline():
    actor=AuthContext(1,192,"staff","manager",frozenset({"tariffs_manage"}),"web")
    result=await forecast_routes.tariff_detail_data("boxing",SimpleNamespace(),actor,Session(SimpleNamespace(id=192,club_settings={"disciplines":{"boxing":{"tariffs":[{"price":100}]}}})))
    assert result["club_id"]==192 and result["tariffs"]==[{"price":100}]
