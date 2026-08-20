from pathlib import Path


def test_shared_web_shell_has_accessible_navigation_language_and_focus_states():
    ui = Path("static/web/components.js").read_text(encoding="utf-8")
    css = Path("static/web/design.css").read_text(encoding="utf-8")
    assert 'aria-label="Primary navigation"' in ui
    assert 'aria-label="Language"' in ui
    assert ":focus-visible" in css
    assert "overflow-x: auto" in css
