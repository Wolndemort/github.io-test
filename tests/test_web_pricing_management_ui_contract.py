from pathlib import Path


def test_staff_pricing_pages_wire_discount_and_tariff_mutations():
    source = Path("static/web/components.js").read_text(encoding="utf-8")
    assert 'location.pathname === "/staff/discounts"' in source
    assert 'location.pathname === "/staff/tariffs"' in source
    for endpoint in ("/api/v1/staff/catalog/discounts", "/api/v1/staff/catalog/tariffs"):
        assert endpoint in source
    assert "JSON.parse(f.get(\"tariffs\"))" in source
    assert "data-discount-create" in source and "data-discount-update" in source
