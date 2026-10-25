from pathlib import Path


def test_email_binding_is_role_agnostic_but_csrf_and_flag_gated():
    source = Path("auth/routes.py").read_text(encoding="utf-8")
    block = source[source.index('@router.post("/native/email/request")'):source.index('@router.post("/native/request")')]
    assert "require_web_context(context)" in block
    assert "require_csrf" in block
    assert "WEB_NATIVE_EMAIL_BINDING_ENABLED" in source
    assert "user.email = email" in block


def test_telegram_profile_offers_native_web_binding_bridge():
    buttons = Path("handlers/buttons.py").read_text(encoding="utf-8")
    routes = Path("auth/routes.py").read_text(encoding="utf-8")
    assert 'text="🔐 Привязать Web-кабинет"' in buttons
    assert "/auth/web-bind?club_id={club_id}" in buttons
    start = routes.index('@router.get("/web-bind", response_class=HTMLResponse)')
    end = routes.index('@router.get("/email-profile"', start)
    bridge = routes[start:end]
    assert "/auth/telegram/exchange" in bridge
    assert 'location.replace("/auth/email-profile")' in bridge
