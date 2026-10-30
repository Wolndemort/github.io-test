from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_advance_debt_warning_is_scheduled_at_opening_time():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    jobs = (ROOT / "services/scheduler_jobs.py").read_text(encoding="utf-8")
    assert "send_advance_debt_morning_notice" in main
    assert "hour=8, minute=30" in main
    assert 'id="advance_debt_morning_warning"' in main
    assert "advance_debt_lessons" in jobs and "advance_debt_days" in jobs


def test_advance_debt_warning_targets_owner_manager_and_cashier_only():
    jobs = (ROOT / "services/scheduler_jobs.py").read_text(encoding="utf-8")
    assert 'ClubStaff.role.in_(["manager", "cashier"])' in jobs
    assert "УТРЕННЕЕ ПРЕДУПРЕЖДЕНИЕ: посещения в долг" in jobs
    assert "сначала погасит этот долг" in jobs
