from pathlib import Path


def test_dangerous_web_mutations_have_confirmation_guard():
    source = Path("static/web/components.js").read_text(encoding="utf-8")
    assert "window.confirm" in source
    assert "Cash|Sell|Activate|Freeze|Cancel|Reverse|Archive" in source
    assert "event.preventDefault()" in source
