from pathlib import Path
from services.schedule_utils import normalize_schedule_block


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


def test_schedule_normalizer_keeps_all_lessons_and_legacy_shapes():
    raw = {
        "mon": {"time": "10:00", "coach": "A", "max_slots": 10, "taken_slots": 2},
        "tue": [
            {"time": "11:00", "coach": "B", "max_slots": 12, "taken_slots": 3},
            {"time": "12:00", "coach": "C", "max_slots": 14, "taken_slots": 4},
        ],
    }
    normalized = normalize_schedule_block(raw)
    assert len(normalized["mon"]) == 1
    assert len(normalized["tue"]) == 2
    assert normalized["tue"][0]["time"] == "11:00"
