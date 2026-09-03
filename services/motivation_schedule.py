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


def motivation_occurrences(settings, start, end):
    result = []
    versions = schedule_versions(settings)
    current = start
    while current <= end:
        disciplines = versions[0][1]
        for effective_from, version_disciplines in versions:
            if effective_from <= current:
                disciplines = version_disciplines
            else:
                break
        day_key = next(key for key, weekday in MOTIVATION_WEEKDAYS.items() if weekday == current.weekday())
        for discipline, block in (disciplines or {}).items():
            lessons = normalize_schedule_block((block or {}).get("schedule", {})).get(day_key, [])
            for lesson in lessons:
                staff_ids = lesson.get("coach_staff_ids") or ([lesson["coach_staff_id"]] if lesson.get("coach_staff_id") else [])
                for staff_id in staff_ids[:5]:
                    result.append({
                        "date": current,
                        "staff_id": int(staff_id),
                        "discipline": discipline,
                        "time": lesson.get("time", ""),
                        "duration_minutes": lesson.get("duration_minutes", 60),
                    })
        current += timedelta(days=1)
    return result
