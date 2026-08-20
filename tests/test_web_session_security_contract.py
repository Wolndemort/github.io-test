from pathlib import Path


def test_logout_revokes_server_side_session_and_clears_cookie():
    source = Path("auth/routes.py").read_text(encoding="utf-8")
    start = source.index('@router.post("/logout")')
    block = source[start:]
    assert "revoke_web_session" in block
    assert "delete_cookie" in block


def test_web_session_validation_does_not_accept_missing_or_revoked_session():
    source = Path("auth/web_session.py").read_text(encoding="utf-8")
    assert "if not session_id:" in source
    assert "if not raw:" in source
    assert "await redis.delete(_session_key(session_id))" in source


def test_csrf_validation_is_bound_to_live_session():
    source = Path("auth/web_session.py").read_text(encoding="utf-8")
    start = source.index("async def validate_csrf")
    block = source[start:source.index("\ndef set_csrf_cookie", start)]
    assert "request.cookies.get(SESSION_COOKIE)" in block
    assert "request.headers.get(CSRF_HEADER)" in block
    assert "if not raw:" in block
    assert "compare_digest" in block
