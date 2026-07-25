import pytest
from pathlib import Path
from pydantic import ValidationError

from admin_module.api import router
from admin_module.schemas import AdminStudentUpdate
from admin_module.system_api import StudentCreate
from database.constants import DEFAULT_CLUB_SETTINGS
from handlers.super_admin_handlers import merge_default_club_settings
from services.input_normalization import normalize_ru_phone, parse_user_date


def route_keys():
    result = set()
    for route in router.routes:
        for method in getattr(route, "methods", None) or {"GET"}:
            result.add((route.path, method))
    return result


def test_club_admin_management_routes_are_registered():
    keys = route_keys()
    assert ("/admin/students", "GET") in keys
    assert ("/admin/students/{student_id}", "PATCH") in keys
    assert ("/admin/students", "POST") in keys
    assert ("/admin/sales", "GET") in keys


def test_admin_product_cash_sale_uses_cart_and_confirms_stock_sale():
    keys = route_keys()
    assert ("/webapp/admin-product-sale", "GET") in keys
    assert ("/webapp/admin-product-sale", "POST") in keys
    api = Path("admin_module/api.py").read_text(encoding="utf-8")
    page = Path("templates/admin_product_sale.html").read_text(encoding="utf-8")
    buttons = Path("handlers/admin_option.py").read_text(encoding="utf-8")
    main_buttons = Path("handlers/buttons.py").read_text(encoding="utf-8")
    assert "status=\"CONFIRMED\"" in api
    assert "product.stock -= quantity" in api
    assert "item_type=\"product\"" in api
    assert "оплату наличными" in page
    assert "admin-product-sale" in buttons
    assert "Продать товар" in main_buttons
    assert '"image_url": p.image_url' in api


def test_admin_sales_and_student_filters_are_present():
    students = Path("templates/admin_students.html").read_text(encoding="utf-8")
    sales = Path("templates/admin_sales.html").read_text(encoding="utf-8")
    assert "student-filter" in students
    assert "data-discipline" in students
    assert "/admin/sales" in students
    assert "payment_method" in sales
    assert "date_from" in sales and "date_to" in sales
    assert "weekday" in sales
    assert "category" in sales
    assert "discipline" in sales
    assert "0–6 лет" in students and "18+ лет" in students
    assert "Выручка за период" in sales
    assert "Средний чек" in sales


def test_stats_and_sales_templates_have_readable_dark_surface_metrics():
    stats = Path("templates/stats.html").read_text(encoding="utf-8")
    css = Path("static/css/mono.css").read_text(encoding="utf-8")
    assert "revenue_today" in stats and "revenue_week" in stats and "revenue_month" in stats
    assert "empty-note" in stats and "muted-note" in stats
    assert ".mono-card .list-item span" in css
    assert "color: #f7f7f7 !important" in css


def test_user_and_admin_history_render_database_utc_as_moscow_time():
    student = Path("templates/webapp_student.html").read_text(encoding="utf-8")
    history = Path("templates/webapp_history.html").read_text(encoding="utf-8")
    admin = Path("admin_module/admin_pages.py").read_text(encoding="utf-8")
    bot = Path("handlers/user_option.py").read_text(encoding="utf-8")
    assert "group_completed_sessions" in bot
    assert "summarize_payment_entry" in bot
    assert "display_last_visit = last_visit_naive + timedelta(hours=3)" in admin
    assert "visit_sessions" in student
    assert "visit_sessions" in history
    assert "payment_lines" in history


def test_background_scheduler_has_stable_moscow_schedule_and_no_overlap():
    source = Path("main.py").read_text(encoding="utf-8")
    assert 'AsyncIOScheduler(timezone=ZoneInfo("Europe/Moscow"))' in source
    for job_id in ("daily_morning_notifications", "expiring_pass_notifications", "daily_admin_report", "auto_close_sessions", "daily_database_backup"):
        assert f'id="{job_id}"' in source
    assert "max_instances=1" in source
    assert "scheduler.shutdown(wait=False)" in source


def test_admin_audit_screen_and_button_are_registered():
    keys = route_keys()
    assert ("/webapp/admin-audit", "GET") in keys
    buttons = Path("handlers/buttons.py").read_text(encoding="utf-8")
    page = Path("templates/admin_audit.html").read_text(encoding="utf-8")
    assert "📜 Аудит" in buttons
    assert "actor_role" in page
    assert "Показываются последние 300 записей" in page


def test_club_settings_reads_are_guarded_and_super_admin_errors_use_logger():
    main = Path("main.py").read_text(encoding="utf-8")
    api = Path("admin_module/api.py").read_text(encoding="utf-8")
    payments = Path("handlers/payments.py").read_text(encoding="utf-8")
    super_handlers = Path("handlers/super_admin_handlers.py").read_text(encoding="utf-8")
    assert "(club.club_settings or {}).get(\"payments\", {})" in main
    scheduler_jobs = Path("services/scheduler_jobs.py").read_text(encoding="utf-8")
    assert "(club.club_settings or {}).get(\"disciplines\", {})" in scheduler_jobs
    assert "(club.club_settings or {}).get(\"disciplines\", {})" in api
    assert "(club.club_settings or {}).get(\"disciplines\", {})" in payments
    assert "(club.club_settings or {}).get(\"payments\", {})" in payments
    assert "print(f\"Ошибка в хендлере extend_club_sub: {e}\")" not in super_handlers
    assert "logger.exception(\"Ошибка в хендлере extend_club_sub\")" in super_handlers


def test_client_cabinet_bottom_bar_is_collapsible_and_uses_high_contrast_actions():
    page = Path("templates/client_cabinet.html").read_text(encoding="utf-8")
    assert "id=\"bottomBar\"" in page
    assert "toggleBottomBar" in page
    assert "Свернуть быстрые действия" in page
    assert "Развернуть быстрые действия" in page
    assert ".bottom-bar.collapsed .inner" in page
    assert ".bottom-bar .btn.soft" in page
    assert ".bottom-bar .btn.dark" in page


def test_schedule_webapp_bottom_bar_is_collapsible():
    page = Path("templates/schedule.html").read_text(encoding="utf-8")
    assert "toggleBottomBar" in page
    assert ".bottom-bar.collapsed .inner" in page
    assert "Свернуть быстрые действия" in page
    assert "Развернуть быстрые действия" in page


def test_master_bot_registers_new_club_bot_without_restart():
    super_source = Path("handlers/super_admin_handlers.py").read_text(encoding="utf-8")
    main = Path("main.py").read_text(encoding="utf-8")
    config = Path("config.py").read_text(encoding="utf-8")
    registry = Path("services/bot_registry.py").read_text(encoding="utf-8")
    assert "register_bot" in super_source
    assert "BASE_URL" in super_source
    assert "register_existing_bots" in main
    assert "close_all_bots" in main
    assert "BASE_URL" in config
    assert "os.getenv('BASE_URL', 'https://speedycrm.ru')" in config
    assert "bots_dict: dict[str, Bot] = {}" in registry
    assert "async def register_bot" in registry


def test_reload_cache_merges_only_missing_fields_and_keeps_manual_settings():
    current = {
        "ui": {"club_name": "Мой клуб", "support_enabled": False},
        "limits": {"session_timeout_minutes": 777},
        "features": {"freeze": False},
    }
    merged, changed = merge_default_club_settings(current, DEFAULT_CLUB_SETTINGS)
    assert changed is True
    assert merged["ui"]["club_name"] == "Мой клуб"
    assert merged["ui"]["support_enabled"] is False
    assert merged["limits"]["session_timeout_minutes"] == 777
    assert merged["features"]["freeze"] is False
    assert "payments" in merged
    assert "disciplines" in merged
    assert merged is not current


def test_daily_student_birthday_reminder_is_scheduled_and_uses_student_flow():
    source = Path("main.py").read_text(encoding="utf-8")
    assert "scheduler.add_job(saas_daily_morning_check" in source
    assert "missing_birthdays" in source
    assert "callback_data=\"edit_birthday\"" in source
    assert "Student.birthday" not in source  # reminder reads Student instances, no parent birthday field is required


def test_admin_can_view_and_update_parent_phone_and_mailing_is_sequenced():
    schema = Path("admin_module/schemas.py").read_text(encoding="utf-8")
    api = Path("admin_module/api.py").read_text(encoding="utf-8")
    page = Path("templates/admin_students.html").read_text(encoding="utf-8")
    main = Path("main.py").read_text(encoding="utf-8")
    assert "parent_phone: str | None = None" in schema
    assert "payload.parent_phone" in api and "normalize_ru_phone" in api
    assert 'name="parent_phone"' in page
    assert "data-filter=\"noParent\"" in page
    assert "hour=10, minute=5" in main
    assert "requestFullscreen" in page


def test_admin_create_student_ui_has_complete_submit_flow():
    page = Path("templates/admin_students.html").read_text(encoding="utf-8")
    assert 'id="saveCreate"' in page
    assert "document.getElementById('saveCreate').onclick = createStudent" in page
    assert "fetch('/admin/students'" in page
    assert "init_data: initData" in page
    assert "list.insertAdjacentHTML('afterbegin', studentCardHtml(s))" in page
    assert "bindEdit(newForm)" in page
    assert "new URLSearchParams(window.location.search).get('init_data')" in page


def test_admin_student_update_requires_club_scope():
    with pytest.raises(ValidationError):
        AdminStudentUpdate(init_data="signed", balance_lessons=10)


def test_bot_manual_add_button_has_registered_flow_and_no_orphan_states():
    buttons = Path("handlers/buttons.py").read_text(encoding="utf-8")
    handler = Path("handlers/admin_students.py").read_text(encoding="utf-8")
    states = Path("handlers/states.py").read_text(encoding="utf-8")
    assert 'callback_data="admin_add_manual"' in buttons
    assert '@router.callback_query(F.data == "admin_add_manual")' in handler
    assert "AdminManualAdd.waiting_for_name" in handler
    assert "AdminManualAdd.waiting_for_phone" in handler
    assert "AdminManualAdd.waiting_for_discipline" in handler
    assert "AdminManualAdd.waiting_for_tariff" in handler
    assert "admin_manual_no_sub_" in handler
    assert "waiting_for_lessons" not in states
    assert "waiting_for_parent_id" not in states


def test_ru_phone_and_date_inputs_are_normalized():
    assert normalize_ru_phone("8 (999) 111-22-33") == "79991112233"
    assert normalize_ru_phone("+7 999 111 22 33") == "79991112233"
    assert normalize_ru_phone("9991112233") == "79991112233"
    assert parse_user_date("15.08.2012").isoformat() == "2012-08-15"
    assert parse_user_date("15082012").isoformat() == "2012-08-15"
    assert parse_user_date("15-08-2012").isoformat() == "2012-08-15"


def test_daily_report_has_no_manual_add_fragment():
    source = Path("handlers/admin_option.py").read_text(encoding="utf-8")
    assert "по тарифу {t_label}" not in source
    assert "sub_expire_str" not in source


def test_admin_student_update_accepts_valid_migration_fields():
    payload = AdminStudentUpdate(
        init_data="signed",
        club_id=7,
        birthday="2012-05-10",
        balance_lessons=999,
        expire_date="2026-12-31",
        can_freeze=1,
        is_frozen=0,
        frozen_days=None,
        discipline="boxing",
    )
    assert payload.club_id == 7
    assert payload.balance_lessons == 999


def test_legacy_student_create_requires_club_id():
    with pytest.raises(ValidationError):
        StudentCreate(name="Атлет", parent_id=10)


def test_legacy_student_create_contains_club_scope():
    payload = StudentCreate(name=" Атлет ", parent_id=10, club_id=7)
    assert payload.club_id == 7
