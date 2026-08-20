from pathlib import Path


def test_discount_assignment_list_and_removal_are_scoped_and_audited():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    start = source.index('@catalog_router.get("/discounts/{discount_id}/assignments")'); block = source[start:]
    for value in ("DiscountAssignment.discount_id == discount_id", "DiscountAssignment.club_id == actor.club_id", "WEB_PRICING_MUTATIONS_ENABLED", "require_csrf", "web:discount-unassign:", "with_for_update()", "web_discount_unassigned"):
        assert value in block
