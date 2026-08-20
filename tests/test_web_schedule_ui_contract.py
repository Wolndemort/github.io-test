from pathlib import Path


def test_schedule_page_has_authenticated_edit_form_and_safe_mount_point():
    source = Path("static/web/components.js").read_text(encoding="utf-8")
    page = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    assert 'location.pathname === "/staff/schedule"' in source
    assert "/api/v1/staff/schedule" in source
    assert 'name="discipline"' in source and 'name="day"' in source
    assert "data-schedule-summary" in page
