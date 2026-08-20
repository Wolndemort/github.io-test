from pathlib import Path


def test_staff_editor_uses_scoped_staff_selector_and_permission_payload():
    source = Path("static/web/components.js").read_text(encoding="utf-8")
    assert 'location.pathname === "/staff/settings/staff"' in source
    assert "/api/v1/staff/settings/staff/data" in source
    assert 'name="permission"' in source
    assert "body.permissions = permissions" in source
    assert "X-CSRF-Token" in source and "crypto.randomUUID()" in source
