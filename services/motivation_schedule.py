from __future__ import annotations

import copy
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from services.schedule_utils import normalize_schedule_block

MOTIVATION_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
MOTIVATION_TZ = ZoneInfo("Europe/Moscow")


def motivation_bonus(rate_row, _student_count=0):
    """Return the administrator-defined fixed bonus for one completed lesson."""
    return max(0, int(getattr(rate_row, "bonus_per_student_kopecks", 0) or 0)) if rate_row else 0


def local_date(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(MOTIVATION_TZ).date()


def utc_boundary(day):
    return datetime.combine(day, datetime.min.time(), tzinfo=MOTIVATION_TZ).astimezone(timezone.utc).replace(tzinfo=None)


def occurrence_ended(occurrence, now=None):
    """Return whether a scheduled training has finished in Moscow time."""
    try:
        hour, minute = (int(part) for part in str(occurrence.get("time", "00:00")).split(":", 1))
        duration = int(occurrence.get("duration_minutes", 60) or 60)
        started = datetime.combine(
            occurrence["date"],
            datetime.min.time().replace(hour=hour, minute=minute),
            tzinfo=MOTIVATION_TZ,
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return False
    current = now or datetime.now(MOTIVATION_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=MOTIVATION_TZ)
    else:
        current = current.astimezone(MOTIVATION_TZ)
    return started + timedelta(minutes=max(15, min(240, duration))) <= current


def schedule_versions(settings):
    current = settings.get("disciplines", {}) if isinstance(settings, dict) else {}
    raw = settings.get("motivation_schedule_history", []) if isinstance(settings, dict) else []
    versions = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get("disciplines"), dict):
                continue
            try:
                effective_from = date.fromisoformat(str(item.get("effective_from")))
            except (TypeError, ValueError):
                continue
            versions.append((effective_from, item["disciplines"]))
    if not versions:
        return [(date.min, current)]
    return sorted(versions, key=lambda item: item[0])


def remember_schedule_change(settings, previous_disciplines, new_disciplines, effective_date=None):
    history = settings.get("motivation_schedule_history")
    if not isinstance(history, list):
        history = []
    effective_date = effective_date or datetime.now(MOTIVATION_TZ).date()
    effective_from = effective_date.isoformat()
    if not history:
        history.append({"effective_from": "1970-01-01", "disciplines": copy.deepcopy(previous_disciplines)})
    history = [item for item in history if isinstance(item, dict) and item.get("effective_from") != effective_from]
    history.append({"effective_from": effective_from, "disciplines": copy.deepcopy(new_disciplines)})
    settings["motivation_schedule_history"] = history[-500:]


def scheduled_lessons(settings, day):
    """Return the effective schedule lessons for one Moscow calendar day."""
    versions = schedule_versions(settings)
    disciplines = versions[0][1]
    for effective_from, version_disciplines in versions:
        if effective_from <= day:
            disciplines = version_disciplines
        else:
            break
    day_key = next(key for key, weekday in MOTIVATION_WEEKDAYS.items() if weekday == day.weekday())
    result = []
    for discipline, block in (disciplines or {}).items():
        lessons = normalize_schedule_block((block or {}).get("schedule", {})).get(day_key, [])
        for index, lesson in enumerate(lessons):
            staff_ids = lesson.get("coach_staff_ids") or ([lesson["coach_staff_id"]] if lesson.get("coach_staff_id") else [])
            result.append({
                "index": index,
                "date": day,
                "staff_ids": [int(staff_id) for staff_id in staff_ids[:5]],
                "discipline": discipline,
                "time": str(lesson.get("time", ""))[:5],
                "duration_minutes": max(15, min(240, int(lesson.get("duration_minutes", 60) or 60))),
                "motivation_rules": lesson.get("motivation_rules") or [],
            })
    return result


def lesson_start(lesson):
    return datetime.combine(
        lesson["date"],
        datetime.min.time().replace(
            hour=int(str(lesson["time"]).split(":", 1)[0]),
            minute=int(str(lesson["time"]).split(":", 1)[1]),
        ),
        tzinfo=MOTIVATION_TZ,
    )


def lesson_duration(lesson):
    return max(15, min(240, int(lesson.get("duration_minutes", 60) or 60)))


def visit_matches_lesson(visit_at, lesson, *, early_minutes=60, late_minutes=20):
    if not visit_at:
        return False
    if visit_at.tzinfo is None:
        visit_at = visit_at.replace(tzinfo=timezone.utc)
    visit_at = visit_at.astimezone(MOTIVATION_TZ)
    started = lesson_start(lesson)
    finished = started + timedelta(minutes=lesson_duration(lesson))
    return started - timedelta(minutes=early_minutes) <= visit_at <= finished + timedelta(minutes=late_minutes)


def lesson_visit_distance(visit_at, lesson):
    if visit_at.tzinfo is None:
        visit_at = visit_at.replace(tzinfo=timezone.utc)
    return abs((visit_at.astimezone(MOTIVATION_TZ) - lesson_start(lesson)).total_seconds())


def motivation_occurrences(settings, start, end):
    result = []
    current = start
    while current <= end:
        for lesson in scheduled_lessons(settings, current):
            for staff_id in lesson["staff_ids"]:
                result.append({key: lesson[key] for key in ("date", "discipline", "time", "duration_minutes")} | {"staff_id": staff_id})
        current += timedelta(days=1)
    return result
