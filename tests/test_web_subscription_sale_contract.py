from pathlib import Path


def test_web_subscription_sale_has_tariff_discount_and_activation_safety():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    start = source.index('@sales_router.post("/cash-subscription")')
    block = source[start:source.index('@catalog_router.get("/discounts")', start)]
    for value in ("WEB_SUBSCRIPTION_SALES_ENABLED", "cash_sale", "require_csrf", "Student.id == student_id", "Student.club_id == actor.club_id", "with_for_update()", "tariff_idx", "active_discounts", "apply_discounts", "add_abon", "PaymentOrder", "CASH_SUBSCRIPTION", "web:subscription-sale:", "web_cash_subscription_sale"):
        assert value in block
