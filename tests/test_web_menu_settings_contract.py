from pathlib import Path


def test_main_menus_and_settings_pages_expose_existing_web_routes():
    ui = Path("static/web/components.js").read_text(encoding="utf-8")
    routes = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    for path in ("/staff/settings/staff", "/staff/settings/notifications", "/staff/settings/disciplines", "/staff/checkin", "/client/students/new", "/client/notifications"):
        assert f'href="{path}"' in ui
        assert f'@web_router.get("{path}"' in routes
    assert "/api/v1/staff/settings/notifications" in ui
