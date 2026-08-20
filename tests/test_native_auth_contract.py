from pathlib import Path


def test_native_email_otp_is_feature_gated_and_uses_server_side_session():
    source = Path("auth/routes.py").read_text(encoding="utf-8")
    native = source[source.index('@router.post("/native/request")'):]
    assert 'WEB_NATIVE_AUTH_ENABLED' in source
    assert 'raise HTTPException(status_code=404' in native
    assert 'create_web_session' in native
    assert 'set_csrf_cookie' in native
    assert 'User.club_id == payload.club_id' in native
