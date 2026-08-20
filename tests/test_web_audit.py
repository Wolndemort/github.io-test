from datetime import datetime
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from auth import forecast_routes
from auth.context import AuthContext

class Result:
    def scalars(self): return self
    def all(self): return [SimpleNamespace(id=1, event="login", action="view", object_type="page", created_at=datetime(2026, 8, 20))]

class Session:
    async def execute(self, statement):
        assert 22 in statement.compile().params.values()
        return Result()

@pytest.mark.asyncio
async def test_audit_data_is_scoped_and_paginated():
    actor = AuthContext(1, 22, "staff", "manager", frozenset({"analytics_view"}), "web")
    result = await forecast_routes.audit_data(SimpleNamespace(), actor, Session(), 10, 5, None, None)
    assert result["club_id"] == 22
    assert result["pagination"] == {"limit": 10, "offset": 5, "returned": 1}
    assert result["read_only"] is True

@pytest.mark.asyncio
async def test_audit_filters_are_returned_in_contract():
    actor = AuthContext(1, 22, "staff", "manager", frozenset({"analytics_view"}), "web")
    result = await forecast_routes.audit_data(SimpleNamespace(), actor, Session(), 10, 0, "web_login", "Manager")
    assert result["filters"] == {"event": "web_login", "actor_role": "Manager"}

@pytest.mark.asyncio
async def test_audit_rejects_missing_permission():
    actor = SimpleNamespace(actor_type="staff", permissions=set())
    with pytest.raises(HTTPException): await forecast_routes.web_audit_page(actor)
