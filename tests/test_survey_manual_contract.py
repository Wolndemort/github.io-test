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


def test_survey_summary_exposes_respondents_for_each_answer_option():
    views = (ROOT / "admin_module/webapp_views.py").read_text(encoding="utf-8")
    template = (ROOT / "templates/admin_surveys.html").read_text(encoding="utf-8")
    assert 'respondents_by_question_value' in views
    assert '"respondents": respondents_by_question_value' in views
    assert 'class="answer-breakdown"' in template
    assert 'o.respondents' in template
    assert "Повторить непройденным" in template
    assert "Повторить недоставленным" in template

def test_answer_options_link_to_prefilled_broadcast_recipients():
    views = (ROOT / "admin_module/webapp_views.py").read_text(encoding="utf-8")
    survey = (ROOT / "templates/admin_surveys.html").read_text(encoding="utf-8")
    broadcast = (ROOT / "templates/admin_broadcast.html").read_text(encoding="utf-8")
    assert '"student_id": delivery_row["student_id"]' in views
    assert "/webapp/admin-broadcast?club_id={{club_id}}&student_ids=" in survey
    assert "initialIds" in broadcast
    assert "prefill_student_ids" in views and "student_ids: str = Query" in views


def test_manual_survey_selection_is_previewed_and_confirmed():
    survey = (ROOT / "templates/admin_surveys.html").read_text(encoding="utf-8")
    assert 'id="selectedRecipientsPreview"' in survey
    assert 'id="resendSelectedPreview"' in survey
    assert "savedRecipientIds" in survey
    assert "Проверка получателей" in survey
    assert "Подтвердить и отправить" in survey
    assert "Сначала выберите хотя бы одного атлета" not in survey
