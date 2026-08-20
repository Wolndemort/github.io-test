from pathlib import Path

def test_shared_table_component_and_empty_state_exist():
    source=Path("static/web/components.js").read_text(encoding="utf-8")
    css=Path("static/web/design.css").read_text(encoding="utf-8")
    assert "table(columns, rows" in source
    assert "web-table" in css and "web-empty" in css
