from pathlib import Path


def test_web_product_sale_uses_scoped_product_buyer_and_discount_selectors():
    source = Path("static/web/components.js").read_text(encoding="utf-8")
    route = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    for endpoint in ("/api/v1/staff/catalog/products", "/api/v1/staff/sales/buyers", "/api/v1/staff/catalog/discounts"):
        assert endpoint in source
    assert 'name="discount_ids" multiple' in source
    assert 'discount_ids: [...event.target.querySelectorAll' in source
    assert '@sales_router.get("/buyers")' in route
    assert 'User.club_id == actor.club_id' in route
