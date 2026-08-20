from pathlib import Path


def test_web_stock_adjustment_has_inventory_safety_contract():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    start = source.index('@catalog_router.post("/products/{product_id}/stock")')
    block = source[start:source.index('@catalog_router.get("/discounts")', start)]
    for value in ("WEB_INVENTORY_MUTATIONS_ENABLED", "products_manage", "require_csrf", "invalid_stock_fields", "ClubProduct.id == product_id", "ClubProduct.club_id == actor.club_id", "with_for_update()", "web:stock:", "insufficient_stock", "web_product_stock_adjusted"):
        assert value in block
