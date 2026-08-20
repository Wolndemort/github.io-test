from pathlib import Path


def test_club_settings_mutation_is_scoped_allowlisted_and_audited():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    start = source.index('@settings_router.patch("/club")'); block = source[start:source.index('@checkin_router.get("/data")', start)]
    for value in ("WEB_SETTINGS_MUTATIONS_ENABLED", "settings_manage", "require_csrf", "web:club-settings:", "with_for_update()", "invalid_settings_fields", "web_club_settings_updated"):
        assert value in block


def test_discount_mutations_are_scoped_permissioned_idempotent_and_audited():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    start = source.index('@catalog_router.post("/discounts")'); block = source[start:source.index('@catalog_router.get("/tariffs")', start)]
    for value in ("WEB_PRICING_MUTATIONS_ENABLED", "tariffs_manage", "require_csrf", "actor.club_id", "with_for_update()", "web:discount-create:", "web:discount-update:", "web_discount_created", "web_discount_updated"):
        assert value in block
