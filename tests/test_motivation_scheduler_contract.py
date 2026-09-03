from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_motivation_accrual_is_registered_as_a_background_job():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    scheduler = (ROOT / "services" / "scheduler_jobs.py").read_text(encoding="utf-8")
    accrual = (ROOT / "services" / "motivation_accrual.py").read_text(encoding="utf-8")
    page = (ROOT / "admin_module" / "webapp_views.py").read_text(encoding="utf-8")

    assert "from services.motivation_accrual import accrue_motivation_job" in scheduler
    assert "scheduler.add_job(accrue_motivation_job, 'interval', minutes=1" in main
    assert "async def accrue_motivation_job" in accrual
    motivation_page = page[page.index('async def admin_motivation_page'):page.index('@router.post("/webapp/admin-motivation/adjust")')]
    assert "session.add(MotivationAccrual" not in motivation_page
    assert "await session.commit()" not in motivation_page


def test_motivation_job_uses_fixed_bonus_and_completion_time():
    accrual = (ROOT / "services" / "motivation_accrual.py").read_text(encoding="utf-8")
    schedule = (ROOT / "services" / "motivation_schedule.py").read_text(encoding="utf-8")

    assert "bonus_kopecks=motivation_bonus(rate_row)" in accrual
    assert "occurrence_ended(occurrence, now_local)" in accrual
    assert "start_time + duration_minutes" in schedule or "started + timedelta(minutes" in schedule
