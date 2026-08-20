from pathlib import Path


def test_operations_ui_uses_csrf_idempotency_and_web_endpoints():
    source = Path("static/web/components.js").read_text(encoding="utf-8")
    assert "speedycrm_csrf_token" in source
    assert "crypto.randomUUID()" in source
    assert "/api/v1/staff/cash/entries" in source
    assert "/api/v1/staff/sales/cash-product" in source
    assert "Operation unavailable or disabled" in source
