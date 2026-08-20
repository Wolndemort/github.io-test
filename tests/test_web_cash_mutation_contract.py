from pathlib import Path


def _block(source, marker, next_marker):
    start = source.index(marker)
    return source[start:source.index(next_marker, start)]


def test_web_cash_entry_has_money_mutation_safety_contract():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    block = _block(source, '@cash_router.post("/entries")', '@cash_router.post("/entries/{entry_id}/reverse")')
    for value in ("WEB_CASH_MUTATIONS_ENABLED", "cash_sale", "require_csrf", "invalid_cash_fields", "amount_kopecks", "web:cash-entry:", "web_cash_entry_created"):
        assert value in block


def test_web_cash_reversal_is_scoped_locked_idempotent_and_audited():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    block = _block(source, '@cash_router.post("/entries/{entry_id}/reverse")', '@sales_router.get("/data")')
    for value in ("WEB_CASH_MUTATIONS_ENABLED", "cash_sale", "require_csrf", "CashEntry.id == entry_id", "CashEntry.club_id == actor.club_id", "with_for_update()", "web:cash-reversal:", "reversed_entry_id", "web_cash_entry_reversed"):
        assert value in block
