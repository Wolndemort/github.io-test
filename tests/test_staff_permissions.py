from pathlib import Path

from types import SimpleNamespace

from handlers.buttons import admin_keyboard, get_profile_keyboard
from services.staff_permissions import ROLE_PERMISSIONS


def test_staff_roles_have_separate_least_privilege_permissions():
    assert ROLE_PERMISSIONS["cashier"] == {"cash_sale", "products_view", "products_manage"}
    assert ROLE_PERMISSIONS["coach"] == {"schedule_view", "schedule_edit", "qr_checkin", "manual_checkin"}
    assert ROLE_PERMISSIONS["manager"] == {
        "cash_sale",
        "products_view",
        "products_manage",
        "cash_view",
        "schedule_view",
        "schedule_edit",
        "tariffs_manage",
        "qr_checkin",
        "manual_checkin",
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


def test_staff_profile_keyboard_hides_qr_and_freeze_but_keeps_admin_full_mode():
    user = SimpleNamespace(user_id=101)
    staff_markup = get_profile_keyboard(user, 7, {"features": {"freeze": True}, "limits": {"freeze_price_per_day": 100}}, profile_mode="staff")
    client_markup = get_profile_keyboard(user, 7, {"features": {"freeze": True}, "limits": {"freeze_price_per_day": 100}}, profile_mode="client")

    staff_texts = [button.text for row in staff_markup.inline_keyboard for button in row]
    client_texts = [button.text for row in client_markup.inline_keyboard for button in row]

    assert "🔓 Открыть турникет" in staff_texts
    assert "📲 QR-пропуск" not in staff_texts
    assert "❄️ Купить заморозку" not in staff_texts
    assert "❄️ Заморозить абонемент" not in staff_texts

    assert "📲 QR-пропуск" in client_texts
    assert "❄️ Купить заморозку" in client_texts
    assert "❄️ Заморозить абонемент" in client_texts


def test_staff_pass_template_and_route_are_present():
    page = Path("templates/staff_pass.html").read_text(encoding="utf-8")
    api = Path("admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")
    settings = Path("handlers/admin_settings_panel.py").read_text(encoding="utf-8")
    assert "Открыть турникет" in page
    assert "FaceID" in page
    assert "/webapp/staff-open-turnstile" in api
    assert "/webapp/staff-pass" in settings
    assert "admin_turnstile_main" in settings


def test_admin_keyboard_exposes_staff_turnstile_entry_for_full_access_and_staff():
    full_markup = admin_keyboard(7, {}, staff_permissions=None)
    staff_markup = admin_keyboard(7, {}, staff_permissions={"cash_view"})

    full_texts = [button.text for row in full_markup.inline_keyboard for button in row]
    staff_texts = [button.text for row in staff_markup.inline_keyboard for button in row]

    assert "🔓 Открыть турникет" in full_texts
    assert "🔓 Открыть турникет" in staff_texts


def test_staff_profile_keyboard_adds_webapp_schedule_entry():
    user = SimpleNamespace(user_id=101)
    staff_markup = get_profile_keyboard(user, 7, {"features": {"schedule": True}}, profile_mode="staff")
    staff_texts = [button.text for row in staff_markup.inline_keyboard for button in row]
    assert any("Расписание (WebApp)" in t for t in staff_texts)
