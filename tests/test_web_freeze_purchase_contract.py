from pathlib import Path


def test_web_freeze_purchase_has_lock_price_discount_and_audit_contract():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    start = source.index('@client_router.post("/freeze/purchase")')
    block = source[start:source.index('@catalog_router.get("/discounts")', start)]
    for value in ("WEB_FREEZE_MUTATIONS_ENABLED", "require_csrf", "Student.id == student_id", "Student.club_id == actor.club_id", "with_for_update()", "freeze_price_per_day", "active_discounts", "apply_discounts", "purchase_student_freeze", "PaymentOrder", "CASH_FREEZE", "web:freeze:", "web_cash_freeze_sale"):
        assert value in block
