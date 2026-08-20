from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from auth import forecast_routes
from auth.context import AuthContext

class Session:
    def __init__(self, student): self.student=student
    async def scalar(self, statement):
        assert 101 in statement.compile().params.values(); return self.student

@pytest.mark.asyncio
async def test_student_detail_requires_same_club():
    actor=AuthContext(1,101,"staff","manager",frozenset({"analytics_view"}),"web")
    student=SimpleNamespace(id=8,name="Kid",discipline="boxing",expire_date=None,balance_lessons=3,is_frozen=False)
    result=await forecast_routes.student_detail_data(8,SimpleNamespace(),actor,Session(student))
    assert result["club_id"]==101 and result["student"]["id"]==8 and result["read_only"] is True

@pytest.mark.asyncio
async def test_student_detail_not_found_is_safe():
    actor=AuthContext(1,101,"staff","manager",frozenset({"analytics_view"}),"web")
    with pytest.raises(HTTPException) as error: await forecast_routes.student_detail_data(999,SimpleNamespace(),actor,Session(None))
    assert error.value.status_code==404
