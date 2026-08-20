from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from auth.forecast_routes import web_forecast_page


@pytest.mark.asyncio
async def test_web_forecast_page_is_available_to_authorized_staff():
    actor = SimpleNamespace(actor_type="staff", permissions={"forecast_view"})
    response = await web_forecast_page(actor)
    assert response.status_code == 200
    assert "/api/v1/staff/forecast/data" in response.body.decode()


@pytest.mark.asyncio
async def test_web_forecast_page_rejects_staff_without_forecast_permission():
    actor = SimpleNamespace(actor_type="staff", permissions=set())
    with pytest.raises(HTTPException) as error:
        await web_forecast_page(actor)
    assert error.value.status_code == 403


def test_web_forecast_is_separate_from_legacy_telegram_forecast():
    source = Path("admin_module/admin_pages.py").read_text(encoding="utf-8")
    assert '@router.get("/forecast"' in source
    assert 'init_data: str | None' in source
