from pathlib import Path


def test_native_email_binding_has_rate_limit_and_role_independent_auth_context():
    source = Path("auth/routes.py").read_text(encoding="utf-8")
    native = source[source.index('@router.post("/native/email/request")'):source.index('@router.post("/native/request")')]
    assert "allow_otp_request" in native
    assert "request.client.host" in native
    assert "actor.club_id" in native
    assert 'WEB_NATIVE_EMAIL_BINDING_ENABLED' in source
