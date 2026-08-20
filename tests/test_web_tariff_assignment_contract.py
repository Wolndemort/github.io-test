from pathlib import Path


def test_tariff_mutation_is_allowlisted_scoped_locked_and_idempotent():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    start = source.index('@catalog_router.patch("/tariffs/{discipline}")'); block = source[start:source.index('@catalog_router.post("/discounts/{discount_id}/assign")', start)]
    for value in ("WEB_PRICING_MUTATIONS_ENABLED", "tariffs_manage", "require_csrf", "invalid_tariff_fields", "web:tariffs:", "with_for_update()", "web_tariffs_updated"):
        assert value in block


def test_discount_assignment_enforces_target_and_club_scope():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    start = source.index('@catalog_router.post("/discounts/{discount_id}/assign")'); block = source[start:]
    for value in ("WEB_PRICING_MUTATIONS_ENABLED", "tariffs_manage", "require_csrf", "one_assignment_target_required", "Discount.club_id == actor.club_id", "User.club_id == actor.club_id", "Student.club_id == actor.club_id", "web:discount-assign:", "web_discount_assigned"):
        assert value in block
