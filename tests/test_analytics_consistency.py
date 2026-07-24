from datetime import datetime, timedelta
from types import SimpleNamespace

from services.analytics import calculate_admin_dashboard, calculate_daily_business_report, calculate_student_metrics


def make_student(index, parent_id=None, balance=8, expire_date=None, frozen=0, last_visit=None):
    return SimpleNamespace(
        id=index,
        name=f"Атлет {index}",
        parent_id=parent_id,
        balance_lessons=balance,
        expire_date=expire_date,
        is_frozen=frozen,
        frozen_at=None,
        last_visit=last_visit,
        discipline="boxing",
        parent_phone=None,
    )


def test_all_reports_count_unlimited_and_no_subscription_athletes():
    now = datetime(2026, 7, 24, 12, 0)
    students = [
        make_student(1, parent_id=10, balance=999, expire_date=now + timedelta(days=30)),
        make_student(2, parent_id=10, balance=0, expire_date=None),
        make_student(3, parent_id=20, balance=5, expire_date=now + timedelta(days=30)),
        make_student(4, parent_id=None, balance=2, expire_date=now + timedelta(days=30)),
    ]

    metrics = calculate_student_metrics(students, now=now)
    dashboard = calculate_admin_dashboard(students)
    daily = calculate_daily_business_report(students, [], [])

    assert metrics["total_athletes"] == 4
    assert metrics["total_parents"] == 2
    assert dashboard["total_athletes"] == 4
    assert dashboard["total_parents"] == 2
    assert daily["total_athletes"] == 4
    assert daily["total_parents"] == 2


def test_revenue_report_accepts_direct_and_cart_payments():
    now = datetime(2026, 7, 24, 12, 0)
    payments = [
        SimpleNamespace(amount_kopecks=100_00, created_at=now),
        SimpleNamespace(amount_kopecks=250_00, created_at=now),
    ]
    result = calculate_daily_business_report([], payments, [])
    assert result["revenue_today"] == 350
