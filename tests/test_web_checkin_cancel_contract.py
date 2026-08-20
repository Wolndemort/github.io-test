from pathlib import Path


def test_web_checkin_cancel_has_full_mutation_safety_contract():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    start = source.index('@checkin_router.post("/cancel")')
    block = source[start:source.index('\n\n@freeze_router', start)]
    assert 'WEB_CHECKIN_CANCEL_ENABLED' in block
    assert 'manual_checkin' in block
    assert 'require_csrf' in block
    assert 'VisitLog.id == visit_id' in block
    assert 'VisitLog.club_id == actor.club_id' in block
    assert 'with_for_update()' in block
    assert 'web:cancel-checkin:' in block
    assert 'web_checkin_cancelled' in block
    assert 'reason' in block
