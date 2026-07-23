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
