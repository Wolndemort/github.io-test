from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_coach_has_manual_checkin_permission():
    source = (ROOT / "services/staff_permissions.py").read_text(encoding="utf-8")
    assert '"coach"' in source and '"manual_checkin"' in source


def test_staff_webapp_has_both_checkin_modes_and_search():
    source = (ROOT / "admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")
    page = (ROOT / "templates/staff_checkin.html").read_text(encoding="utf-8")
    assert "/webapp/staff-checkin/search" in source
    assert "open_turnstile" in source
    assert "open_turnstile=bool" in source
    assert "Без турникета" in page and "С турникетом" in page
    assert "encodeURIComponent(q)" in page


def test_manual_bot_checkin_uses_no_relay_mode():
    source = (ROOT / "handlers/admin_option.py").read_text(encoding="utf-8")
    assert "open_turnstile=False" in source
