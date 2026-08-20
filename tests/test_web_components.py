from pathlib import Path


def test_shared_web_components_provide_navigation_loading_error_and_json_client():
    source = Path("static/web/components.js").read_text(encoding="utf-8")
    assert "navigation" in source
    assert "loading" in source
    assert "error" in source
    assert "async json" in source


def test_forecast_uses_shared_web_components():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    assert "/static/web/components.js" in source
    assert "SpeedyCRMWeb.navigation" in source
    assert "SpeedyCRMWeb.json" in source
