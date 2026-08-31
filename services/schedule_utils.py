from __future__ import annotations

DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def normalize_schedule_block(schedule_data) -> dict[str, list[dict]]:
    normalized: dict[str, list[dict]] = {day: [] for day in DAY_KEYS}
    if not isinstance(schedule_data, dict):
        return normalized

    for day in DAY_KEYS:
        raw_day = schedule_data.get(day, [])
        lessons: list[dict] = []
        if isinstance(raw_day, dict):
            raw_day = [raw_day]
        if not isinstance(raw_day, list):
            continue
        for lesson in raw_day:
            if not isinstance(lesson, dict):
                continue
            raw_max = lesson.get("max_slots", lesson.get("slots", lesson.get("limit", 0)))
            try:
                max_slots = int(raw_max if raw_max is not None else 0)
            except (ValueError, TypeError):
                max_slots = 0
            raw_taken = lesson.get("taken_slots", 0)
            try:
                taken_slots = int(raw_taken if raw_taken is not None else 0)
            except (ValueError, TypeError):
                taken_slots = 0
            normalized_lesson = {
                "time": str(lesson.get("time", "00:00"))[:5],
                "coach": str(lesson.get("coach", lesson.get("info", "")))[:100],
                "max_slots": max(0, max_slots),
                "taken_slots": max(0, taken_slots),
            }
            if lesson.get("coach_staff_id") is not None:
                try:
                    normalized_lesson["coach_staff_id"] = int(lesson["coach_staff_id"])
                except (TypeError, ValueError):
                    pass
            lessons.append(normalized_lesson)
        lessons.sort(key=lambda x: x["time"])
        normalized[day] = lessons
    return normalized
