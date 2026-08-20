from pathlib import Path


def test_shared_navigation_has_persistent_en_ru_switcher():
    source = Path("static/web/components.js").read_text(encoding="utf-8")
    assert 'data-web-language' in source
    assert 'localStorage.setItem("speedycrm_language"' in source
    assert 'document.documentElement.lang' in source
