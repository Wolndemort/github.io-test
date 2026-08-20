from pathlib import Path


def test_web_product_sale_has_money_inventory_and_discount_safety_contract():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    start = source.index('@sales_router.post("/cash-product")')
    block = source[start:source.index('@catalog_router.get("/discounts")', start)]
    for value in ("WEB_PRODUCT_SALES_ENABLED", "cash_sale", "require_csrf", "invalid_sale_fields", "User.club_id == actor.club_id", "with_for_update()", "active_discounts", "apply_discounts", "insufficient_stock", "CartOrder", "CartItem", "status=\"CONFIRMED\"", "CASH:", "web:product-sale:", "web_cash_product_sale"):
        assert value in block
