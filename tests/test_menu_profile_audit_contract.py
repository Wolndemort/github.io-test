from pathlib import Path


def test_start_and_profile_flow_emits_audit_events():
    start = Path("handlers/start.py").read_text(encoding="utf-8")
    user = Path("handlers/user_option.py").read_text(encoding="utf-8")

    assert "bot_menu_opened" in start
    assert "bot_profile_opened" in start
    assert "bot_menu_opened" in user
    assert "bot_profile_opened" in user
    assert "bot_profile_details_opened" in user


def test_card_management_flow_emits_audit_events():
    payments = Path("handlers/payments.py").read_text(encoding="utf-8")

    assert "bot_card_management_opened" in payments
    assert "bot_card_delete_confirm_opened" in payments
    assert "bot_card_deleted" in payments
    assert "sub.is_active = False" not in payments


def test_admin_audit_screen_is_exposed_in_webapp_and_super_panel():
    api = Path("admin_module/api.py").read_text(encoding="utf-8")
    super_handlers = Path("handlers/super_admin_handlers.py").read_text(encoding="utf-8")
    buttons = Path("handlers/buttons.py").read_text(encoding="utf-8")

    assert "/webapp/admin-audit" in api
    assert "super_audit" in super_handlers
    assert "web_app=types.WebAppInfo(url=f\"{base_url}/webapp/admin-audit?club_id={club_id}\")" in buttons or "webapp/admin-audit" in buttons
