from pathlib import Path

def test_shared_breadcrumb_component_exists():
    source=Path("static/web/components.js").read_text(encoding="utf-8")
    assert "breadcrumb(items)" in source
    assert "web-breadcrumb" in Path("static/web/design.css").read_text(encoding="utf-8")
