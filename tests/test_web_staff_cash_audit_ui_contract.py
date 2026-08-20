from pathlib import Path


def test_staff_cash_reversal_and_audit_detail_links_are_functional():
    api = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    ui = Path("static/web/components.js").read_text(encoding="utf-8")
    assert '"entries": [{"id": entry.id' in api
    assert 'location.pathname === "/staff/cash"' in ui
    assert "/api/v1/staff/cash/entries/" in ui
    assert 'location.pathname === "/staff/audit"' in ui
    assert "/staff/audit/${entry.id}" in ui
