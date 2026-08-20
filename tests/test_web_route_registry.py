from pathlib import Path


def test_web_route_modules_are_registered_in_main():
    source = Path("main.py").read_text(encoding="utf-8")
    assert "auth_router" in source
    assert "forecast_router" in source
    assert "client_router" in source
    assert "settings_router" in source
    assert "checkin_router" in source


def test_phase0_and_readonly_entrypoints_exist_in_source():
    auth = Path("auth/routes.py").read_text(encoding="utf-8")
    routes = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    assert 'router.get("/login")' in auth
    assert 'router.post("/telegram/exchange")' in auth
    assert 'router.get("/me")' in auth
    assert 'router.post("/logout")' in auth
    assert '@web_router.get("/staff"' in routes
    assert '@web_router.get("/client/hub"' in routes


def test_mutations_are_flagged_and_legacy_telegram_routes_are_preserved():
    routes = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    assert 'WEB_SCHEDULE_MUTATIONS_ENABLED' in routes
    assert 'WEB_CLIENT_STUDENT_MUTATIONS_ENABLED' in routes
    assert 'WEB_CLIENT_BIND_PHONE_ENABLED' in routes
    legacy = Path("admin_module/api.py").read_text(encoding="utf-8")
    assert 'verify_webapp_staff' in legacy
    assert 'init_data' in legacy
