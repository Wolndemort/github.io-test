from pathlib import Path


def test_web_manual_checkin_has_full_mutation_safety_contract():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    start = source.index('@checkin_router.post("/manual")')
    block = source[start:source.index('\n\n@freeze_router', start)]
    assert 'WEB_CHECKIN_MUTATIONS_ENABLED' in block
    assert 'manual_checkin' in block
    assert 'require_csrf' in block
    assert 'expected_club_id=actor.club_id' in block
    assert 'web:checkin:' in block
    assert 'invalid_checkin_fields' in block
    assert 'web_manual_checkin' in block
    assert 'open_turnstile' in block
