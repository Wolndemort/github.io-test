import pytest
from pathlib import Path
from pydantic import ValidationError

from admin_module.api import router
from admin_module.schemas import AdminStudentUpdate
from admin_module.system_api import StudentCreate
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
    assert "visit.visited_at.replace(tzinfo=None) + timedelta(hours=3)" in bot
    assert "display_last_visit = last_visit_naive + timedelta(hours=3)" in admin
    assert "visited_at|moscow_time" in student
    assert "created_at|moscow_time" in history


def test_background_scheduler_has_stable_moscow_schedule_and_no_overlap():
    source = Path("main.py").read_text(encoding="utf-8")
    assert 'AsyncIOScheduler(timezone=ZoneInfo("Europe/Moscow"))' in source
    for job_id in ("daily_morning_notifications", "expiring_pass_notifications", "daily_admin_report", "auto_close_sessions", "daily_database_backup"):
        assert f'id="{job_id}"' in source
    assert "max_instances=1" in source
    assert "scheduler.shutdown(wait=False)" in source


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
    handler = Path("handlers/admin_option.py").read_text(encoding="utf-8")
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
