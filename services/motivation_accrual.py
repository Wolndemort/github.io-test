from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from database.db import (
    AsyncSessionLocal,
    Club,
    ClubStaff,
    MotivationAccrual,
    MotivationRate,
    Student,
    VisitLog,
)
from services.motivation_schedule import (
    MOTIVATION_TZ,
    local_date,
    motivation_bonus,
    motivation_occurrences,
    occurrence_ended,
    utc_boundary,
)

LOOKBACK_DAYS = 366


def _scheduled_rate(rate_row, training_date, fallback=0):
    if not rate_row:
        return int(fallback or 0)
    specific = rate_row.weekend_rate_kopecks if training_date.weekday() >= 5 else rate_row.weekday_rate_kopecks
    return int(specific or rate_row.rate_kopecks or 0)


def _attendee_count(visit_rows, occurrence):
    try:
        hour, minute = (int(value) for value in str(occurrence["time"]).split(":", 1))
    except (KeyError, TypeError, ValueError):
        return 0
    slot_minutes = hour * 60 + minute
    duration = max(15, min(240, int(occurrence.get("duration_minutes", 60) or 60)))
    students = set()
    for visit, discipline in visit_rows:
        if str(discipline or "") != str(occurrence["discipline"]):
            continue
        visited = visit.visited_at
        if not visited:
            continue
        local_visited = visited.replace(tzinfo=timezone.utc).astimezone(MOTIVATION_TZ) if visited.tzinfo is None else visited.astimezone(MOTIVATION_TZ)
        if local_date(visited) != occurrence["date"]:
            continue
        visit_minutes = local_visited.hour * 60 + local_visited.minute
        if slot_minutes - 60 <= visit_minutes <= slot_minutes + duration + 60:
            students.add(visit.student_id)
    return len(students)


def _occurrence_key(club_id, occurrence):
    return f"{club_id}:{occurrence['date'].isoformat()}:{occurrence['time']}:{occurrence['discipline']}:{occurrence['staff_id']}"


async def accrue_completed_motivation(session, club, now=None):
    """Persist every finished scheduled lesson not yet present in accruals."""
    now_local = now or datetime.now(MOTIVATION_TZ)
    if now_local.tzinfo is None:
        now_local = now_local.replace(tzinfo=MOTIVATION_TZ)
    else:
        now_local = now_local.astimezone(MOTIVATION_TZ)
    today = now_local.date()
    start = today - timedelta(days=LOOKBACK_DAYS)
    settings = club.club_settings or {}
    occurrences = motivation_occurrences(settings, start, today)
    visit_rows = (
        await session.execute(
            select(VisitLog, Student.discipline)
            .join(Student, Student.id == VisitLog.student_id)
            .where(
                VisitLog.club_id == club.id,
                VisitLog.visited_at >= utc_boundary(start),
                VisitLog.visited_at < utc_boundary(today + timedelta(days=1)),
            )
        )
    ).all()
    rates = (await session.execute(select(MotivationRate).where(MotivationRate.club_id == club.id))).scalars().all()
    staff = (await session.execute(select(ClubStaff).where(ClubStaff.club_id == club.id))).scalars().all()
    staff_by_id = {item.id: item for item in staff}
    existing = (
        await session.execute(
            select(MotivationAccrual).where(
                MotivationAccrual.club_id == club.id,
                MotivationAccrual.occurrence_date >= start,
                MotivationAccrual.occurrence_date <= today,
            )
        )
    ).scalars().all()
    existing_keys = {item.occurrence_key for item in existing}
    rate_by_key = {(item.staff_id, item.discipline): item for item in rates}
    added = 0
    for occurrence in occurrences:
        key = _occurrence_key(club.id, occurrence)
        if key in existing_keys or not occurrence_ended(occurrence, now_local):
            continue
        rate_row = rate_by_key.get((occurrence["staff_id"], occurrence["discipline"]))
        coach = staff_by_id.get(occurrence["staff_id"])
        students = _attendee_count(visit_rows, occurrence)
        session.add(
            MotivationAccrual(
                club_id=club.id,
                staff_id=occurrence["staff_id"],
                occurrence_key=key,
                occurrence_date=occurrence["date"],
                start_time=occurrence["time"],
                discipline=occurrence["discipline"],
                rate_kopecks=_scheduled_rate(rate_row, occurrence["date"], getattr(coach, "rate_per_training_kopecks", 0)),
                student_count=students,
                bonus_kopecks=motivation_bonus(rate_row),
            )
        )
        existing_keys.add(key)
        added += 1
    if added:
        await session.commit()
    return added


async def accrue_motivation_job():
    """APScheduler entry point; catches up missed lessons after restarts too."""
    async with AsyncSessionLocal() as session:
        clubs = (await session.execute(select(Club).where(Club.bot_token.is_not(None)))).scalars().all()
        for club in clubs:
            try:
                added = await accrue_completed_motivation(session, club)
                if added:
                    from logging import getLogger
                    getLogger("uvicorn.error").info("Motivation accruals added: club=%s count=%s", club.id, added)
            except Exception:
                await session.rollback()
                from logging import getLogger
                getLogger("uvicorn.error").exception("Motivation accrual job failed for club=%s", club.id)
