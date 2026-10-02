from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from database.db import AsyncSessionLocal, Club, MotivationAccrual, Student, VisitLog

MOSCOW = ZoneInfo("Europe/Moscow")
DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _utc_naive(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def _local_datetime(day: date, time_text: str) -> datetime:
    hour, minute = (int(part) for part in str(time_text).split(":", 1))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=MOSCOW)


def _rules_for(lesson: dict, student_count: int) -> dict | None:
    for rule in sorted(lesson.get("motivation_rules") or [], key=lambda item: int(item.get("min_students", 0))):
        if int(rule.get("min_students", 0)) <= student_count <= int(rule.get("max_students", 0)):
            return rule
    return None


async def accrue_finished_lessons(now: datetime | None = None) -> int:
    now_local = (now or datetime.now(MOSCOW)).astimezone(MOSCOW)
    today = now_local.date()
    weekday = DAY_KEYS[today.weekday()]
    added = 0
    async with AsyncSessionLocal() as session:
        clubs = (await session.execute(select(Club).where(Club.subscription_expire_at >= datetime.now(timezone.utc).replace(tzinfo=None)))).scalars().all()
        for club in clubs:
            settings = club.club_settings if isinstance(club.club_settings, dict) else {}
            for discipline, block in (settings.get("disciplines", {}) or {}).items():
                for index, lesson in enumerate(((block or {}).get("schedule", {}) or {}).get(weekday, []) or []):
                    time_text = str(lesson.get("time", "")).strip()[:5]
                    try:
                        started = _local_datetime(today, time_text)
                    except (TypeError, ValueError):
                        continue
                    # Считаем после начала занятия + 15 минут допуска.
                    if now_local < started + timedelta(minutes=15):
                        continue
                    staff_ids = [int(x) for x in (lesson.get("coach_staff_ids") or ([lesson.get("coach_staff_id")] if lesson.get("coach_staff_id") else []))]
                    if not staff_ids:
                        continue
                    occurrence_key = f"{club.id}:{today.isoformat()}:{discipline}:{index}:{time_text}"
                    exists = await session.scalar(select(MotivationAccrual.id).where(MotivationAccrual.occurrence_key == occurrence_key))
                    if exists:
                        continue
                    start_utc = (started - timedelta(minutes=60)).astimezone(timezone.utc).replace(tzinfo=None)
                    end_utc = (started + timedelta(minutes=15)).astimezone(timezone.utc).replace(tzinfo=None)
                    rows = (await session.execute(
                        select(VisitLog.student_id).join(Student, Student.id == VisitLog.student_id).where(
                            VisitLog.club_id == club.id,
                            VisitLog.visited_at >= start_utc,
                            VisitLog.visited_at <= end_utc,
                            Student.discipline == discipline,
                        ).distinct()
                    )).all()
                    student_count = len(rows)
                    rule = _rules_for(lesson, student_count)
                    if not rule:
                        continue
                    session.add(MotivationAccrual(
                        club_id=club.id, discipline=discipline, occurrence_key=occurrence_key,
                        occurrence_date=today, start_time=time_text, student_count=student_count,
                        rule_min_students=int(rule["min_students"]), rule_max_students=int(rule["max_students"]),
                        rate_kopecks=int(rule["rate_kopecks"]), staff_ids=staff_ids,
                    ))
                    added += 1
        await session.commit()
    return added


async def motivation_accrual_job():
    await accrue_finished_lessons()
