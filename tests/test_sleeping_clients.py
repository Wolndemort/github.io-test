from datetime import datetime, timedelta
from types import SimpleNamespace

from services.analytics import calculate_student_metrics


def test_sleeping_clients_use_visit_history_even_without_active_subscription():
    now = datetime(2026, 8, 31, 12, 0)
    students = [
        SimpleNamespace(id=1, parent_id=10, balance_lessons=0, expire_date=None, is_frozen=0, last_visit=None, discipline="boxing"),
        SimpleNamespace(id=2, parent_id=10, balance_lessons=5, expire_date=now + timedelta(days=10), is_frozen=0, last_visit=None, discipline="boxing"),
    ]
    visits = [SimpleNamespace(student_id=1, visited_at=now - timedelta(days=20)), SimpleNamespace(student_id=2, visited_at=now - timedelta(days=20))]
    metrics = calculate_student_metrics(students, now=now, visit_logs=visits)
    assert {student.id for student in metrics["sleeping_students"]} == {1, 2}


def test_new_client_without_visit_is_not_sleeping():
    now = datetime(2026, 8, 31, 12, 0)
    student = SimpleNamespace(id=1, parent_id=10, balance_lessons=5, expire_date=now + timedelta(days=10), is_frozen=0, last_visit=None, discipline="boxing")
    assert calculate_student_metrics([student], now=now, visit_logs=[])["sleeping_students"] == []
