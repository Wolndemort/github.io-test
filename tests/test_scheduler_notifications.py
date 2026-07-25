from pathlib import Path
from services.schedule_utils import normalize_schedule_block


ROOT = Path(__file__).parents[1]


def test_scheduler_has_all_required_client_notification_flows():
    source = (ROOT / "services" / "scheduler_jobs.py").read_text(encoding="utf-8")
    assert "notify:no-subscription" in source
    assert "notify:birthday-missing" in source
    assert "notify:birthday:" in source
    assert "notify:absent:" in source
    assert "days_absent >= 10" in source
    assert "Указать дату рождения" in source
    assert "Выбрать абонемент" in source
    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "scheduler.add_job(saas_daily_morning_check, 'cron', hour=10, minute=0" in main_source
    assert "scheduler.add_job(check_abon_mailing, 'cron', hour=10, minute=5" in main_source


def test_expiring_scheduler_requires_real_active_subscription():
    source = (ROOT / "database" / "db.py").read_text(encoding="utf-8")
    assert "Student.parent_id.is_not(None)" in source
    assert "Student.balance_lessons > 0" in source
    assert "Student.balance_lessons <= 2" in source
    assert "Student.expire_date <= three_days_limit" in source


def test_expiring_reminders_cover_days_and_lessons():
    source = (ROOT / "services" / "scheduler_jobs.py").read_text(encoding="utf-8")
    assert "_subscription_reminder_flags" in source
    assert "days_left in {3, 2, 1}" in source
    assert "Осталось занятий" in source
    assert "notify:expire:" in source


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
