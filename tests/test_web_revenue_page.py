from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from auth.forecast_routes import web_revenue_page


@pytest.mark.asyncio
async def test_web_revenue_page_uses_shared_shell_for_authorized_staff():
    actor = SimpleNamespace(actor_type="staff", permissions={"analytics_view"})
    response = await web_revenue_page(actor)
    body = response.body.decode()
    assert response.status_code == 200
    assert "/static/web/design.css" in body
    assert "/api/v1/staff/revenue/data" in body


@pytest.mark.asyncio
async def test_web_revenue_page_rejects_staff_without_analytics_permission():
    actor = SimpleNamespace(actor_type="staff", permissions=set())
    with pytest.raises(HTTPException) as error:
        await web_revenue_page(actor)
    assert error.value.status_code == 403


def test_revenue_page_uses_common_components():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    assert "SpeedyCRMWeb.navigation(\"Staff web / Revenue\")" in source
    assert "SpeedyCRMWeb.json(\"/api/v1/staff/revenue/data\")" in source
