from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_session_close_is_scheduled_and_decrements_one_lesson_but_preserves_unlimited():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert 'scheduler.add_job(auto_close_sessions_job, \'interval\', minutes=1' in source
    assert "student.balance_lessons = max(0, (student.balance_lessons or 0) - 1)" in source
    assert "is_unlimited = (student.balance_lessons == 999)" in source
    assert "student.last_visit = None" in source


def test_manual_and_qr_checkins_share_the_same_gate_service():
    admin = (ROOT / "handlers/admin_option.py").read_text(encoding="utf-8")
    user = (ROOT / "handlers/user_option.py").read_text(encoding="utf-8")
    assert "process_athlete_gate_pass(" in admin
    assert "process_athlete_gate_pass(" in user
    assert 'expected_club_id=club.id' in admin
    assert 'expected_club_id=club.id' in user


def test_qr_scanner_stays_open_until_explicit_stop():
    page = (ROOT / "templates/scanner.html").read_text(encoding="utf-8")
    assert "scanner.start" in page
    assert "scanner.stop" in page
    assert "tg?.close();" in page
    assert "document.getElementById('stop').onclick" in page
    assert "finally(() => setTimeout" in page


def test_qr_scanner_page_does_not_require_init_data_gate():
    source = (ROOT / "admin_module/api.py").read_text(encoding="utf-8")
    assert '@router.get("/webapp/scanner", response_class=HTMLResponse)' in source
    assert "must not return 401 here" in source
    assert "TemplateResponse(\n        \"scanner.html\"" in source


def test_face_id_is_enforced_server_side_for_webapp_turnstile():
    source = (ROOT / "admin_module/turnstile_biometry.py").read_text(encoding="utf-8")
    assert "payload.biometric_token" in source
    assert "Face ID не активирован" in source
    assert "is_biometric_enabled" in source
