from pathlib import Path


def test_subscription_and_freeze_ui_use_authenticated_selectors():
    source = Path("static/web/components.js").read_text(encoding="utf-8")
    route = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    assert "/api/v1/staff/sales/options" in source
    assert "/api/v1/client/cabinet/data" in source
    assert 'name="tariff"' in source and 'name="student_id"' in source
    assert '@sales_router.get("/options")' in route
    assert 'Student.club_id == actor.club_id' in route
