from pathlib import Path


def test_client_profile_exposes_phone_binding_with_safe_input_and_endpoint():
    source = Path("static/web/components.js").read_text(encoding="utf-8")
    route = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    assert 'name="phone" type="tel" inputmode="tel" autocomplete="tel"' in source
    assert "/api/v1/client/bind-phone" in source
    assert '@client_router.post("/bind-phone")' in route
    assert "WEB_CLIENT_BIND_PHONE_ENABLED" in route
    assert "require_csrf" in route
    assert "rate_key = f\"web:bind-phone" in route


def test_native_client_renderer_covers_cabinet_profile_pass_and_schedule():
    source = Path("static/web/client.js").read_text(encoding="utf-8")
    for path in ("/client/cabinet", "/client/me", "/client/pass", "/client/schedule"):
        assert path in source
    for endpoint in ("/api/v1/client/cabinet/data", "/api/v1/client/me", "/api/v1/client/pass/data", "/api/v1/client/schedule/data"):
        assert endpoint in source
    assert "image_data_url" in source
    assert "SpeedyCRMWeb.table" in source
