from pathlib import Path


def test_staff_management_page_and_integrations_never_accept_secrets():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    assert '@web_router.get("/staff/settings/staff"' in source
    start = source.index('@settings_router.patch("/integrations")')
    block = source[start:source.index('@checkin_router.get("/data")', start)]
    assert "WEB_SETTINGS_MUTATIONS_ENABLED" in block
    assert "email_enabled" in block and "push_enabled" in block
    assert "SMTP_PASSWORD" not in block
    assert "bot_token" not in block
    assert "YANDEX_SECRET" not in block
