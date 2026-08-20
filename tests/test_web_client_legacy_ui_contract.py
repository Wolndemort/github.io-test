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
