from datetime import datetime, timedelta
from types import SimpleNamespace

from services.visit_history import attach_student_names, group_completed_sessions, payment_status_label, summarize_payment_entry


def test_group_completed_sessions_collapses_repeat_checkins_and_drops_active_session():
    now = datetime(2026, 7, 25, 12, 0, 0)
    visits = [
        SimpleNamespace(visited_at=now - timedelta(hours=4), student_id=1),
        SimpleNamespace(visited_at=now - timedelta(hours=3, minutes=50), student_id=1),
        SimpleNamespace(visited_at=now - timedelta(hours=2, minutes=30), student_id=1),
        SimpleNamespace(visited_at=now - timedelta(minutes=20), student_id=1),
    ]

    grouped = group_completed_sessions(visits, timeout_minutes=60, now=now)

    assert len(grouped) == 2
    assert grouped[0]["visits_count"] == 1
    assert grouped[1]["visits_count"] == 2
    assert grouped[0]["ended_at"] > grouped[0]["started_at"]


def test_summarize_payment_entry_and_status_labels():
    order = SimpleNamespace(created_at=datetime(2026, 7, 25, 10, 30), amount_kopecks=150000, status="CONFIRMED", lesson_count=8, days_to_add=30)
    line = summarize_payment_entry(order, student_name="Аня")

    assert "Абонемент" in line
    assert "Аня" in line
    assert "1500 ₽" in line
    assert payment_status_label("NEW") == "⏳ ожидает"


def test_attach_student_names_produces_groupable_rows():
    rows = attach_student_names([SimpleNamespace(visited_at=datetime(2026, 7, 25, 9, 0), student_id=7)], {7: "Петя"})
    grouped = group_completed_sessions(rows, timeout_minutes=30, now=datetime(2026, 7, 25, 10, 0))
    assert grouped[0]["student_name"] == "Петя"
