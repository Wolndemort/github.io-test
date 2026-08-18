from datetime import datetime, timedelta
from types import SimpleNamespace

from services.analytics import calculate_projected_renewal_revenue


def test_projected_revenue_uses_recent_expired_and_due_students_and_popular_tariff():
    now = datetime(2026, 8, 18, 12)
    students = [
        SimpleNamespace(id=1, name="Expired", expire_date=datetime(2026, 8, 10), last_visit=datetime(2026, 8, 12)),
        SimpleNamespace(id=2, name="Due", expire_date=datetime(2026, 9, 5), last_visit=datetime(2026, 8, 17)),
        SimpleNamespace(id=3, name="Too old", expire_date=datetime(2026, 9, 1), last_visit=datetime(2026, 8, 1)),
    ]
    visits = [SimpleNamespace(student_id=1, visited_at=datetime(2026, 8, 12)), SimpleNamespace(student_id=2, visited_at=datetime(2026, 8, 17))]
    settings = {"disciplines": {"boxing": {"tariffs": [{"name": "8 занятий", "count": 8, "days": 30, "price": 4000}, {"name": "12 занятий", "count": 12, "days": 30, "price": 5500}]}}}
    payments = [SimpleNamespace(status="CONFIRMED", discipline="boxing", lesson_count=12, days_to_add=30, amount_kopecks=550000)] * 2
    result = calculate_projected_renewal_revenue(students, visits, payments, settings, "2026-08-05", "2026-09-05", now)
    assert result["count"] == 2
    assert result["projected_revenue"] == 11000
    assert "boxing: 12 занятий" in result["tariff_name"]
    assert result["price_source"] == "самый продаваемый подтверждённый тариф по каждому направлению"


def test_projected_revenue_rejects_reversed_period():
    try:
        calculate_projected_renewal_revenue([], date_from="2026-09-05", date_to="2026-08-05")
    except ValueError as error:
        assert "date_to" in str(error)
    else:
        raise AssertionError("expected ValueError")


def test_expiry_end_date_is_inclusive_and_recent_visit_boundary_is_inclusive():
    now = datetime(2026, 8, 18, 12)
    student = SimpleNamespace(id=10, name="Boundary", expire_date=datetime(2026, 9, 5, 0), last_visit=None)
    visit = SimpleNamespace(student_id=10, visited_at=now - timedelta(days=14))
    result = calculate_projected_renewal_revenue(
        [student], [visit], [], {"disciplines": {}}, "2026-08-05", "2026-09-05", now
    )
    assert result["count"] == 1


def test_old_visit_and_expiry_after_window_are_excluded():
    now = datetime(2026, 8, 18, 12)
    students = [
        SimpleNamespace(id=1, expire_date=datetime(2026, 9, 5), last_visit=now - timedelta(days=15)),
        SimpleNamespace(id=2, expire_date=datetime(2026, 9, 6), last_visit=now - timedelta(days=2)),
    ]
    result = calculate_projected_renewal_revenue(students, now=now, date_from="2026-08-05", date_to="2026-09-05")
    assert result["count"] == 0


def test_latest_visit_log_overrides_stale_student_cache():
    now = datetime(2026, 8, 18, 12)
    student = SimpleNamespace(id=1, expire_date=datetime(2026, 9, 1), last_visit=now - timedelta(days=30))
    visit = SimpleNamespace(student_id=1, visited_at=now - timedelta(days=2))
    result = calculate_projected_renewal_revenue([student], [visit], now=now, date_to="2026-09-01")
    assert result["count"] == 1


def test_no_tariffs_has_zero_forecast_and_explains_price_source():
    now = datetime(2026, 8, 18, 12)
    student = SimpleNamespace(id=1, expire_date=datetime(2026, 9, 1), last_visit=now - timedelta(days=1))
    result = calculate_projected_renewal_revenue([student], now=now, date_to="2026-09-01")
    assert result["count"] == 1
    assert result["projected_revenue"] == 0
    assert result["price"] == 0
    assert "нет" in result["price_source"]


def test_timezone_aware_visit_is_normalized():
    from datetime import timezone

    now = datetime(2026, 8, 18, 12)
    student = SimpleNamespace(id=1, expire_date=datetime(2026, 9, 1), last_visit=None)
    visit = SimpleNamespace(student_id=1, visited_at=datetime(2026, 8, 17, 15, tzinfo=timezone.utc))
    result = calculate_projected_renewal_revenue([student], [visit], now=now, date_to="2026-09-01")
    assert result["count"] == 1


def test_discipline_tariff_is_selected_by_sales_count_not_highest_price():
    now = datetime(2026, 8, 18, 12)
    student = SimpleNamespace(id=1, discipline="bjj", expire_date=datetime(2026, 9, 1), last_visit=now - timedelta(days=1))
    settings = {"disciplines": {"bjj": {"tariffs": [
        {"name": "8 посещений", "count": 8, "days": 30, "price": 5000},
        {"name": "12 посещений", "count": 12, "days": 30, "price": 7000},
        {"name": "Безлимит", "count": 999, "days": 30, "price": 10000},
    ]}}}
    payments = [SimpleNamespace(status="CONFIRMED", discipline="bjj", lesson_count=8, days_to_add=30, amount_kopecks=500000)] * 20
    payments += [SimpleNamespace(status="CONFIRMED", discipline="bjj", lesson_count=12, days_to_add=30, amount_kopecks=700000)] * 19
    payments += [SimpleNamespace(status="CONFIRMED", discipline="bjj", lesson_count=999, days_to_add=30, amount_kopecks=1000000)] * 3
    result = calculate_projected_renewal_revenue([student], payments=payments, club_settings=settings, now=now, date_to="2026-09-01")
    assert result["tariffs_by_discipline"]["bjj"] == {"name": "8 посещений", "price": 5000.0, "count": 8}
    assert result["projected_revenue"] == 5000
