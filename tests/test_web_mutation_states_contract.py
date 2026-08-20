from pathlib import Path


def test_web_mutation_forms_expose_loading_state_and_restore_controls():
    source = Path("static/web/components.js").read_text(encoding="utf-8")
    assert 'button.disabled = true' in source
    assert 'button.textContent = "Saving…"' in source
    assert 'target.setAttribute("aria-busy", "true")' in source
    assert 'target.removeAttribute("aria-busy")' in source
