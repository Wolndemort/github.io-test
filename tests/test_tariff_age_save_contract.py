from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_tariff_update_treats_zero_max_age_as_unbounded_and_has_readable_error():
    source = (ROOT / "admin_module/webapp_views.py").read_text(encoding="utf-8")
    page = (ROOT / "templates/admin_tariffs.html").read_text(encoding="utf-8")
    assert 'tariff["max_age"] = _as_max_age(tariff.get("max_age"))' in source
    assert "Тариф #{index + 1}: проверьте цену, срок, занятия и возраст от/до" in source
    assert 'button[data-save]' in page
    assert 'data-save="${i}"' in page
