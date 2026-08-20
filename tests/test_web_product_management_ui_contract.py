from pathlib import Path


def test_staff_product_page_wires_create_stock_and_archive_actions():
    source = Path("static/web/components.js").read_text(encoding="utf-8")
    assert 'location.pathname === "/staff/products"' in source
    for value in ("/api/v1/staff/catalog/products", "/stock`,", "method: \"DELETE\"", "crypto.randomUUID()", "X-CSRF-Token"):
        assert value in source
    assert "data-product-create" in source and "data-stock-adjust" in source and "data-product-archive" in source
