from types import SimpleNamespace
import pytest
from auth import forecast_routes
from auth.context import AuthContext

class Session:
    async def scalar(self, statement):
        params=statement.compile().params; assert 211 in params.values() and 3 in params.values()
        return SimpleNamespace(id=3,club_id=211,event="view",action="read",object_type="page",created_at=None,payload={"safe":"yes","secret":"no"})

@pytest.mark.asyncio
async def test_audit_detail_is_scoped_and_redacted():
    actor=AuthContext(1,211,"staff","manager",frozenset({"analytics_view"}),"web")
    result=await forecast_routes.audit_detail_data(3,SimpleNamespace(),actor,Session())
    assert result["club_id"]==211 and result["entry"]["payload"]["safe"]=="yes" and "secret" not in result["entry"]["payload"]

@pytest.mark.asyncio
async def test_audit_detail_page_requires_permission():
    from fastapi import HTTPException
    with pytest.raises(HTTPException): await forecast_routes.web_audit_detail_page(3,SimpleNamespace(actor_type="staff",permissions=set()))
