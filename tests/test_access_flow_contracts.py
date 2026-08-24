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


def test_admin_and_coach_have_explicit_qr_entry_and_permission():
    buttons = (ROOT / "handlers/buttons.py").read_text(encoding="utf-8")
    perms = (ROOT / "services/staff_permissions.py").read_text(encoding="utf-8")
    admin = (ROOT / "handlers/admin_option.py").read_text(encoding="utf-8")

    assert 'scanner_url = f"https://{club_id}.speedycrm.ru/webapp/scanner?club_id={club_id}&v=108"' in buttons
    assert 'text="📸 ОТКРЫТЬ СКАНЕР (ВХОД)"' in buttons
    assert '"coach": {"schedule_view", "schedule_edit", "qr_checkin", "manual_checkin"}' in perms
    assert '"manager": {"cash_sale", "products_view", "products_manage", "cash_view", "schedule_view", "schedule_edit", "tariffs_manage", "qr_checkin", "manual_checkin", "forecast_view", "analytics_view", "athletes_view", "athletes_manage", "student_manage"}' in perms
    assert 'if not (is_owner or is_super_admin or (staff and "qr_checkin" in permissions_for_staff(staff)))' in admin


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


def test_qr_scanner_enforces_club_feature_flag_and_generation_is_parent_scoped():
    api = (ROOT / "admin_module/api.py").read_text(encoding="utf-8")
    user = (ROOT / "handlers/user_option.py").read_text(encoding="utf-8")
    assert 'qr_checkin", True' in api
    assert "StudentParent.parent_id == callback.from_user.id" in user
    assert "Student.parent_id == callback.from_user.id" in user


def test_face_id_is_enforced_server_side_for_webapp_turnstile():
    source = (ROOT / "admin_module/turnstile_biometry.py").read_text(encoding="utf-8")
    assert "payload.biometric_token" in source
    assert "Face ID не активирован" in source
    assert "is_biometric_enabled" in source


def test_face_id_activation_handles_android_clients_without_init_callback():
    page = (ROOT / "templates/biometric_pass.html").read_text(encoding="utf-8")
    endpoint = (ROOT / "admin_module/turnstile_biometry.py").read_text(encoding="utf-8")
    assert "bm.init();" in page
    assert "isBiometricReady = true" in page
    assert "Face ID activation failed" in page
    assert "biometric_enable_requested" in endpoint
    assert "biometric_enabled" in endpoint


def test_payment_webhook_uses_verified_payment_method_data():
    source = (ROOT / "admin_module/payments_webhook.py").read_text(encoding="utf-8")
    assert 'payment_method = verified_saas_payment.get("payment_method") or {}' in source
    assert 'payment_method = verified_payment.get("payment_method") or {}' in source
    assert 'payment_method = object_data.get("payment_method")' not in source


def test_client_subscription_flow_supports_secondary_parent_links():
    source = (ROOT / "admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")
    start = source.index('@router.get("/webapp/client-cabinet/buy-subscription"')
    end = source.index('@router.get("/webapp/client-cabinet/auth"', start)
    block = source[start:end]
    assert "StudentParent.parent_id == user.user_id" in block
    assert "StudentParent.parent_id == user_id" in block
