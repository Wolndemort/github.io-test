from pathlib import Path


def test_camera_settings_are_safe_and_never_accept_device_credentials():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    start = source.index('@settings_router.patch("/camera")'); block = source[start:source.index('@settings_router.get("/features")', start)]
    for value in ("WEB_SETTINGS_MUTATIONS_ENABLED", "settings_manage", "require_csrf", "web:camera:", "with_for_update()", "invalid_camera_address", "web_camera_updated"):
        assert value in block
    assert "password" not in block.lower()
    assert "token" not in block.lower()
    assert "secret" not in block.lower()
