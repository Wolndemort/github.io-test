from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_scheduler_has_all_required_client_notification_flows():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "notify:no-subscription" in source
    assert "notify:birthday-missing" in source
    assert "notify:birthday:" in source
    assert "notify:absent:" in source
    assert "days_absent >= 10" in source
    assert "Указать дату рождения" in source
    assert "Выбрать абонемент" in source


def test_expiring_scheduler_requires_real_active_subscription():
    source = (ROOT / "database" / "db.py").read_text(encoding="utf-8")
    assert "Student.parent_id.is_not(None)" in source
    assert "Student.balance_lessons > 0" in source
    assert "Student.expire_date <= three_days_limit" in source
