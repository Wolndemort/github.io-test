from pathlib import Path


def test_staff_freeze_web_uses_date_to_and_current_freezes():
    source = Path('static/web/freeze.js').read_text(encoding='utf-8')
    assert 'name="date_to" type="date"' in source
    assert '/api/v1/staff/freeze/apply' in source
    assert '/api/v1/staff/freeze/unfreeze' in source
    assert 'frozen_until' in source
    assert 'idempotency_key' in source


def test_staff_freeze_api_accepts_date_to_without_changing_legacy_days():
    source = Path('auth/forecast_routes.py').read_text(encoding='utf-8')
    block = source[source.index('@freeze_router.post("/apply")'):source.index('@freeze_router.post("/unfreeze")')]
    assert '"date_to"' in block
    assert 'date.fromisoformat(date_to)' in block
    assert 'process_student_freeze' in block
    assert 'with_for_update()' in block
