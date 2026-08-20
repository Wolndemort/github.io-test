from pathlib import Path


def test_notifications_and_disciplines_are_allowlisted_scoped_and_idempotent():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    for marker, audit, redis_key in (("@settings_router.patch(\"/notifications\")", "web_notifications_updated", "web:notifications:"), ("@settings_router.patch(\"/disciplines\")", "web_disciplines_updated", "web:disciplines:")):
        start = source.index(marker); end = source.find("\n\n@", start + 2); block = source[start:] if end < 0 else source[start:end]
        for value in ("WEB_SETTINGS_MUTATIONS_ENABLED", "settings_manage", "require_csrf", "with_for_update()", redis_key, audit):
            assert value in block
    assert "telegram_enabled" in source
    assert "invalid_discipline_fields" in source
