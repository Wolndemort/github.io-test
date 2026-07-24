from pathlib import Path

from services.staff_permissions import ROLE_PERMISSIONS


def test_staff_roles_have_separate_least_privilege_permissions():
    assert "products_manage" in ROLE_PERMISSIONS["cashier"]
    assert "schedule_edit" in ROLE_PERMISSIONS["coach"]
    assert "products_manage" in ROLE_PERMISSIONS["manager"]
    assert "schedule_edit" in ROLE_PERMISSIONS["manager"]
    for role in ROLE_PERMISSIONS:
        assert "athletes_view" not in ROLE_PERMISSIONS[role]
        assert "settings_manage" not in ROLE_PERMISSIONS[role]
        assert "payments_manage" not in ROLE_PERMISSIONS[role]


def test_staff_webapp_actions_require_signed_init_data_and_permission():
    api = Path("admin_module/api.py").read_text(encoding="utf-8")
    utils = Path("admin_module/utils.py").read_text(encoding="utf-8")
    assert "verify_webapp_staff" in api
    assert "if not club or not getattr(club, \"bot_token\", None) or not init_data" in utils
    assert '"products_manage"' in api
    assert '"schedule_edit"' in api
    assert '"qr_checkin"' in api


def test_staff_management_supports_owner_and_super_admin_and_delete():
    source = Path("handlers/admin_option.py").read_text(encoding="utf-8")
    super_source = Path("handlers/super_admin_handlers.py").read_text(encoding="utf-8")
    assert "staff_delete_" in source
    assert "is_super_admin" in source
    assert "super_staff_add" in super_source
    assert "ClubStaff" in source and "ClubStaff" in super_source
