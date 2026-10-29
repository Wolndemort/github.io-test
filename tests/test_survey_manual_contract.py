from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_surveys_support_one_time_mode_without_scheduler():
    views = (ROOT / "admin_module/webapp_views.py").read_text(encoding="utf-8")
    template = (ROOT / "templates/admin_surveys.html").read_text(encoding="utf-8")
    assert "scheduler_enabled" in views
    assert "trigger_days: int | None" in views
    assert "Без планировщика" in template or "без планировщика" in template
    assert "schedulerEnabled" in template


def test_manual_and_resend_routes_have_redis_claims_and_new_trigger_keys():
    views = (ROOT / "admin_module/webapp_views.py").read_text(encoding="utf-8")
    service = (ROOT / "services/surveys.py").read_text(encoding="utf-8")
    template = (ROOT / "templates/admin_surveys.html").read_text(encoding="utf-8")
    assert "/webapp/admin-surveys/{survey_id}/send" in views
    assert "/webapp/admin-surveys/{survey_id}/resend" in views
    assert "survey:manual:" in views
    assert "survey:resend:" in views
    assert "trigger_prefix" in service
    assert "Повторить непройденным" in template
    assert "Повторить недоставленным" in template
