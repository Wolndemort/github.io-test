from pathlib import Path


def test_email_binding_is_role_agnostic_but_csrf_and_flag_gated():
    source = Path("auth/routes.py").read_text(encoding="utf-8")
    block = source[source.index('@router.post("/native/email/request")'):source.index('@router.post("/native/request")')]
    assert "require_web_context(context)" in block
    assert "require_csrf" in block
    assert "WEB_NATIVE_EMAIL_BINDING_ENABLED" in source
    assert "user.email = email" in block
