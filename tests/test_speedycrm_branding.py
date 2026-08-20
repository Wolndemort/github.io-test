from pathlib import Path

def test_generated_web_layer_uses_speedycrm_branding():
    files=(Path("auth/forecast_routes.py"),Path("static/web/components.js"),Path("static/web/design.css"))
    text="\n".join(path.read_text(encoding="utf-8") for path in files)
    assert "SpeedyCRM" in text
    assert "ALTER" not in text
    assert "AlterWeb" not in text
