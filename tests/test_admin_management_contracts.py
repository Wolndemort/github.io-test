import pytest
from pydantic import ValidationError

from admin_module.api import router
from admin_module.schemas import AdminStudentUpdate
from admin_module.system_api import StudentCreate


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


def test_admin_student_update_requires_club_scope():
    with pytest.raises(ValidationError):
        AdminStudentUpdate(init_data="signed", balance_lessons=10)


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
