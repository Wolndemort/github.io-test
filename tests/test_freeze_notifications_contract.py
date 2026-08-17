from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_freeze_notification_events_are_covered():
    webapp = (ROOT / "admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")
    bot = (ROOT / "handlers/user_option.py").read_text(encoding="utf-8")
    scheduler = (ROOT / "services/scheduler_jobs.py").read_text(encoding="utf-8")
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "Абонемент заморожен администратором" in webapp
    assert "Абонемент разморожен администратором" in webapp
    assert "Досрочная разморозка при посещении" in bot
    assert "async def expire_student_freezes" in scheduler
    assert "id=\"expire_student_freezes\"" in main
    assert ".with_for_update(skip_locked=True)" in scheduler
    assert "Заморозка завершена" in scheduler
