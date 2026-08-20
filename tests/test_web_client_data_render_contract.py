from pathlib import Path


def test_client_pages_render_returned_records_not_only_a_counter():
    source = Path("static/web/components.js").read_text(encoding="utf-8")
    for endpoint in ("/api/v1/client/history/data", "/api/v1/client/subscriptions/data", "/api/v1/client/purchases/data", "/api/v1/client/products/data", "/api/v1/client/discounts/data"):
        assert endpoint in source
    assert "web-table" in source
    assert "Object.entries(data).filter(([, value]) => Array.isArray(value))" in source
    assert "escapeHtml" in source
