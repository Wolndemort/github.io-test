from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_staff_webapp_does_not_require_client_phone_binding():
    source = (ROOT / "admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")
    assert "if not user and not is_staff_mode" in source
    assert "_is_staff_webapp_user" in source


def test_staff_hiring_shows_identity_and_notifies_staff():
    admin = (ROOT / "handlers/admin_option.py").read_text(encoding="utf-8")
    super_admin = (ROOT / "handlers/super_admin_handlers.py").read_text(encoding="utf-8")
    assert "get_chat" in admin and "Username:" in admin
    assert "Вы приняты в команду" in admin
    assert "Вы уволены из клуба" in admin
    assert "get_chat" in super_admin and "Вы приняты в команду" in super_admin


def test_staff_list_refreshes_names_from_telegram():
    admin = (ROOT / "handlers/admin_option.py").read_text(encoding="utf-8")
    assert 'actual_name = getattr(chat, "full_name", None)' in admin
    assert "if names_changed:" in admin


def test_staff_is_separate_from_student_parent_statistics():
    source = (ROOT / "database/db.py").read_text(encoding="utf-8")
    analytics = (ROOT / "services/analytics.py").read_text(encoding="utf-8")
    assert "class ClubStaff" in source
    assert "Student.parent_id" not in analytics
