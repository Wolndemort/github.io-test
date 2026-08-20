from pathlib import Path

def test_navigation_contains_shared_core_links():
    source = Path("static/web/components.js").read_text(encoding="utf-8")
    assert "/staff/overview" in source
    assert "/staff/forecast" in source
    assert "/staff/revenue" in source
    assert "/staff/students" in source

def test_shared_error_replacement_helper_exists():
    source = Path("static/web/components.js").read_text(encoding="utf-8")
    assert "replaceWithError" in source
