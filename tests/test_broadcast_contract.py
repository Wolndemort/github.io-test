from pathlib import Path


AUTH = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
BOT = Path("handlers/admin_option.py").read_text(encoding="utf-8")
SERVICE = Path("services/broadcast.py").read_text(encoding="utf-8")
UI = Path("static/web/broadcast.js").read_text(encoding="utf-8")
COMPONENTS = Path("static/web/components.js").read_text(encoding="utf-8")


def test_broadcast_recipients_are_club_scoped_and_searchable():
    assert "@router.get(\"/broadcast/recipients\")" in AUTH
    assert "broadcast_recipients_for_student_search" in AUTH
    assert "Student.club_id == club_id" in SERVICE
    assert "Student.parent_phone.ilike" in SERVICE
    assert "Student.parent_phone_secondary.ilike" in SERVICE


def test_web_broadcast_supports_all_and_selected_student_recipients():
    assert 'selected_ids = payload.get("student_ids")' in AUTH
    assert "resolve_broadcast_recipients(session, actor.club_id, selected_ids)" in AUTH
    assert "selected_student_ids=selected_ids" in AUTH
    assert "await resolve_broadcast_recipients(session, club.id)" in BOT
    assert "user_ids.difference_update(staff_ids)" in SERVICE


def test_broadcast_webapp_has_search_selection_and_progress_flow():
    assert "data-broadcast-search" in UI
    assert "data-student-id" in UI
    assert "Рассылка выбранным" in UI
    assert "Рассылка всем" in UI
    assert "payload.student_ids = [...selected]" in UI
    assert "data-broadcast-progress" in UI
    assert 'broadcast.js?v=20261025' in COMPONENTS


def test_broadcast_webapp_has_collapsible_audience_filters():
    assert "data-broadcast-filters" in UI
    assert "data-filter-no-subscription" in UI
    assert "data-filter-no-visits" in UI
    assert "data-filter-discipline" in UI
    assert "data-select-visible" in UI
    assert "last_visit" in SERVICE
    assert "expire_date" in SERVICE
