from pathlib import Path

from services.staff_permissions import ROLE_PERMISSIONS


def test_staff_roles_have_separate_least_privilege_permissions():
    assert ROLE_PERMISSIONS["cashier"] == {"cash_sale", "products_view", "products_manage"}
    assert ROLE_PERMISSIONS["coach"] == {"schedule_view", "schedule_edit", "qr_checkin"}
    assert ROLE_PERMISSIONS["manager"] == {
        "cash_sale",
        "products_view",
        "products_manage",
        "cash_view",
        "schedule_view",
        "schedule_edit",
        "tariffs_manage",
        "qr_checkin",
    }
    for role in ROLE_PERMISSIONS:
        assert "athletes_view" not in ROLE_PERMISSIONS[role]
        assert "settings_manage" not in ROLE_PERMISSIONS[role]
        assert "payments_manage" not in ROLE_PERMISSIONS[role]
        assert "staff_manage" not in ROLE_PERMISSIONS[role]


def test_permissions_for_staff_normalizes_roles_and_respects_custom_deny():
    class Staff:
        role = " Manager "
        is_active = True
        permissions = {"allow": ["athletes_view"], "deny": ["cash_view"]}

    from services.staff_permissions import permissions_for_staff, staff_can

    perms = permissions_for_staff(Staff())
    assert "athletes_view" in perms
    assert "cash_view" not in perms
    assert staff_can(Staff(), "schedule_edit") is True
    assert staff_can(Staff(), "cash_view") is False


def test_unknown_role_gets_no_default_permissions():
    class Staff:
        role = "intern"
        is_active = True
        permissions = {}

    from services.staff_permissions import permissions_for_staff

    assert permissions_for_staff(Staff()) == set()


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


def test_manager_tariff_webapp_is_present_and_uses_shared_settings():
    api = Path("admin_module/api.py").read_text(encoding="utf-8")
    page = Path("templates/admin_tariffs.html").read_text(encoding="utf-8")
    assert '"tariffs_manage"' in api
    assert "/webapp/admin-tariffs" in api
    assert 'settings["disciplines"]' in api
    assert "tg.initData" in page
    assert "/webapp/admin-tariffs/change" in page
