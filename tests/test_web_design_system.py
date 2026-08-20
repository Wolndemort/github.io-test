from pathlib import Path


def test_shared_web_design_tokens_and_responsive_shell_exist():
    css = Path("static/web/design.css").read_text(encoding="utf-8")
    assert "--web-bg" in css
    assert ".web-shell" in css
    assert ".web-card" in css
    assert "@media (max-width: 720px)" in css


def test_forecast_page_uses_shared_design_system():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    assert "/static/web/design.css" in source
    assert "web-hero" in source
    assert "web-grid" in source
