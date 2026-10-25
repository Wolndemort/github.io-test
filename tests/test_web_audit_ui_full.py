from pathlib import Path


def test_native_audit_ui_has_filters_pagination_and_detail_links():
    source = Path('static/web/audit.js').read_text(encoding='utf-8')
    assert '/api/v1/staff/audit/data?' in source
    assert 'name="event"' in source and 'name="actor_role"' in source
    assert 'data-next' in source and 'data-prev' in source
    assert '/staff/audit/${e.id}' in source
