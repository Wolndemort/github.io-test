import os
from services.age_rules import age_matches, age_on, tariff_age_matches
from services.schedule_utils import normalize_schedule_block
from services.motivation_schedule import motivation_bonus, nearest_lesson_for_visit, occurrence_ended, remember_schedule_change, schedule_versions, visit_matches_lesson

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from admin_module.webapp_views import (  # noqa: E402
    _attendee_count,
    _motivation_local_date,
    _motivation_occurrences,
    _motivation_bonus,
    _parse_motivation_time,
    _scheduled_rate,
)
from datetime import date, datetime, timezone
from types import SimpleNamespace


def test_schedule_normalizer_preserves_multiple_coaches():
    schedule = normalize_schedule_block({"mon": [{"time": "18:00", "coach_staff_ids": [7, "8", 7, 0]}]})
    assert schedule["mon"][0]["coach_staff_ids"] == [7, 8]


def test_schedule_normalizer_preserves_motivation_rules_when_schedule_is_saved():
    schedule = normalize_schedule_block({"mon": [{"time": "18:00", "motivation_rules": [{"min_students": 1, "max_students": 10, "rate_kopecks": 500}]}]})
    assert schedule["mon"][0]["motivation_rules"][0]["rate_kopecks"] == 500


def test_age_is_calculated_from_birthday_on_the_visit_date():
    birthday = date(2014, 9, 10)
    assert age_on(birthday, date(2026, 9, 9)) == 11
    assert age_on(birthday, date(2026, 9, 10)) == 12
    assert age_matches(birthday, date(2026, 9, 10), enabled=True, minimum=12, maximum=13)
    assert not age_matches(None, date(2026, 9, 10), enabled=True, minimum=12, maximum=13)
    assert tariff_age_matches(birthday, date(2026, 9, 10), {"min_age": 12, "max_age": 13})
    assert not tariff_age_matches(date(2000, 1, 1), date(2026, 9, 10), {"min_age": 12, "max_age": 13})


def test_occurrences_create_one_payable_row_per_assigned_coach():
    settings = {"disciplines": {"boxing": {"schedule": {"mon": [{"time": "18:00", "coach_staff_ids": [7, 8]}]}}}}
    rows = _motivation_occurrences(settings, date(2026, 8, 31), date(2026, 8, 31))
    assert {(row["staff_id"], row["discipline"], row["time"]) for row in rows} == {(7, "boxing", "18:00"), (8, "boxing", "18:00")}


def test_occurrence_ends_after_start_plus_duration():
    occurrence = {"date": date(2026, 9, 3), "time": "18:00", "duration_minutes": 90}
    assert occurrence_ended(occurrence, datetime(2026, 9, 3, 19, 29)) is False
    assert occurrence_ended(occurrence, datetime(2026, 9, 3, 19, 31)) is True


def test_visit_window_uses_duration_and_fifteen_minute_late_limit():
    lesson = {"date": date(2026, 9, 3), "time": "18:00", "duration_minutes": 90}
    assert visit_matches_lesson(datetime(2026, 9, 3, 15, 0), lesson) is True  # 18:00 MSK
    assert visit_matches_lesson(datetime(2026, 9, 3, 16, 51), lesson) is False  # 19:51 MSK
    assert visit_matches_lesson(datetime(2026, 9, 3, 16, 15), lesson, late_minutes=15) is True  # 19:15 MSK
    assert visit_matches_lesson(datetime(2026, 9, 3, 16, 46), lesson, late_minutes=15) is False  # 19:46 MSK


def test_nearest_same_discipline_lesson_wins_over_a_later_lesson():
    lessons = [
        {"date": date(2026, 9, 3), "time": "17:00", "duration_minutes": 60, "discipline": "jiu_jitsu"},
        {"date": date(2026, 9, 3), "time": "18:00", "duration_minutes": 60, "discipline": "jiu_jitsu"},
        {"date": date(2026, 9, 3), "time": "17:00", "duration_minutes": 60, "discipline": "boxing"},
    ]
    selected = nearest_lesson_for_visit(datetime(2026, 9, 3, 14, 18), lessons, "jiu_jitsu", date(2014, 9, 3))
    assert selected["time"] == "17:00"
    assert nearest_lesson_for_visit(datetime(2026, 9, 3, 12, 0), lessons, "jiu_jitsu") is None


def test_nearest_lesson_rejects_age_mismatch_and_missing_birthday():
    lessons = [{"date": date(2026, 9, 3), "time": "17:00", "duration_minutes": 60, "discipline": "jiu_jitsu", "age_restriction_enabled": True, "age_min": 6, "age_max": 9}]
    visit = datetime(2026, 9, 3, 14, 10)
    assert nearest_lesson_for_visit(visit, lessons, "jiu_jitsu", date(2018, 9, 3)) is not None
    assert nearest_lesson_for_visit(visit, lessons, "jiu_jitsu", date(2014, 9, 3)) is None
    assert nearest_lesson_for_visit(visit, lessons, "jiu_jitsu", None) is None


def test_visit_window_accepts_a_fifteen_minute_late_entry_by_default():
    lesson = {"date": date(2026, 9, 3), "time": "18:00", "duration_minutes": 60}
    assert visit_matches_lesson(datetime(2026, 9, 3, 15, 15), lesson) is True  # 18:15 MSK


def test_schedule_history_uses_old_snapshot_before_change_and_new_after_change():
    settings = {
        "disciplines": {"boxing": {"schedule": {"mon": [{"time": "19:00", "coach_staff_id": 8} ]}}},
        "motivation_schedule_history": [
            {"effective_from": "1970-01-01", "disciplines": {"boxing": {"schedule": {"mon": [{"time": "18:00", "coach_staff_id": 7}]}}}},
            {"effective_from": "2026-09-01", "disciplines": {"boxing": {"schedule": {"mon": [{"time": "19:00", "coach_staff_id": 8}]}}}},
        ],
    }
    old_rows = _motivation_occurrences(settings, date(2026, 8, 31), date(2026, 8, 31))
    new_rows = _motivation_occurrences(settings, date(2026, 9, 1), date(2026, 9, 7))
    assert {(row["staff_id"], row["time"]) for row in old_rows} == {(7, "18:00")}
    assert {(row["staff_id"], row["time"]) for row in new_rows} == {(8, "19:00")}


def test_schedule_history_replaces_same_day_snapshot_without_losing_previous_versions():
    settings = {"disciplines": {}}
    old = {"boxing": {"schedule": {}}}
    first = {"boxing": {"schedule": {"mon": [{"time": "18:00"}]}}}
    second = {"boxing": {"schedule": {"mon": [{"time": "19:00"}]}}}
    remember_schedule_change(settings, old, first, date(2026, 9, 1))
    remember_schedule_change(settings, first, second, date(2026, 9, 1))
    versions = schedule_versions(settings)
    assert [item[0] for item in versions] == [date(1970, 1, 1), date(2026, 9, 1)]
    assert versions[-1][1] == second


def test_attendee_count_deduplicates_student_and_uses_moscow_time():
    visit = SimpleNamespace(student_id=11, visited_at=datetime(2026, 8, 31, 21, 5))  # 00:05 MSK, next day
    rows = [(visit, "boxing"), (visit, "boxing")]
    occurrence = {"date": date(2026, 9, 1), "discipline": "boxing", "time": "00:00"}
    assert _attendee_count(rows, occurrence) == 1


def test_attendee_count_ignores_repeat_gate_passes():
    repeat = SimpleNamespace(student_id=12, visited_at=datetime(2026, 8, 31, 21, 5), source="repeat")
    rows = [(repeat, "boxing")]
    occurrence = {"date": date(2026, 9, 1), "discipline": "boxing", "time": "00:00"}
    assert _attendee_count(rows, occurrence) == 0


def test_scheduled_rate_prefers_weekday_weekend_and_falls_back_to_staff_rate():
    rate = SimpleNamespace(rate_kopecks=10000, weekday_rate_kopecks=12000, weekend_rate_kopecks=15000)
    assert _scheduled_rate(rate, date(2026, 9, 1)) == 12000
    assert _scheduled_rate(rate, date(2026, 9, 5)) == 15000
    assert _scheduled_rate(None, date(2026, 9, 1), 12500) == 12500


def test_bonus_is_fixed_per_completed_lesson_and_not_per_student():
    rate = SimpleNamespace(bonus_threshold=5, bonus_per_student_kopecks=2500)
    assert motivation_bonus(None, 0) == 0
    assert motivation_bonus(rate, 0) == 2500
    assert motivation_bonus(rate, 2) == 2500
    assert motivation_bonus(rate, 100) == 2500
    assert _motivation_bonus(rate, 2) == 2500


def test_motivation_uses_moscow_calendar_date_for_utc_timestamp():
    value = datetime(2026, 8, 31, 21, 30, tzinfo=timezone.utc)
    assert _motivation_local_date(value) == date(2026, 9, 1)


def test_individual_time_is_normalized_and_invalid_time_rejected():
    assert _parse_motivation_time("8:05") == "08:05"
    try:
        _parse_motivation_time("25:00")
    except Exception as error:
        assert getattr(error, "status_code", None) == 400
    else:
        raise AssertionError("invalid time must be rejected")
