from pathlib import Path

def test_logout_is_post_and_csrf_protected_in_shared_navigation():
    source=Path("static/web/components.js").read_text(encoding="utf-8")
    assert "data-web-logout" in source
    assert '"/auth/logout"' in source
    assert 'method: "POST"' in source
    assert "speedycrm_csrf_token" in source
    assert "SpeedyCRMWeb.logout()" in source
    assert "async logout()" in source
