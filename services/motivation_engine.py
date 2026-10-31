from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from database.db import AsyncSessionLocal, Club, ClubStaff, MotivationAccrual, Student, VisitLog
from services.bot_registry import bots_dict
from services.motivation_schedule import lesson_duration, lesson_start, lesson_visit_distance, scheduled_lessons, visit_matches_lesson

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
    """Accrue only completed scheduled lessons from unique gate visits."""
    now_local = (now or datetime.now(MOSCOW)).astimezone(MOSCOW)
    today = now_local.date()
    added = 0
    created_items = []
    async with AsyncSessionLocal() as session:
        clubs = (await session.execute(select(Club).where(Club.subscription_expire_at >= datetime.now(timezone.utc).replace(tzinfo=None)))).scalars().all()
        for club in clubs:
            settings = club.club_settings if isinstance(club.club_settings, dict) else {}
            lessons = scheduled_lessons(settings, today)
            if not lessons:
                continue
            day_start = datetime.combine(today, datetime.min.time(), tzinfo=MOSCOW).astimezone(timezone.utc).replace(tzinfo=None)
            day_end = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=MOSCOW).astimezone(timezone.utc).replace(tzinfo=None)
            visit_rows = (await session.execute(
                select(VisitLog, Student.discipline).join(Student, Student.id == VisitLog.student_id).where(
                    VisitLog.club_id == club.id, VisitLog.visited_at >= day_start, VisitLog.visited_at < day_end,
                )
            )).all()
            motivation = settings.get("motivation") if isinstance(settings.get("motivation"), dict) else {}
            early_minutes = max(0, min(240, int(motivation.get("early_entry_minutes", 60) or 60)))
            late_minutes = max(0, min(60, int(motivation.get("late_entry_minutes", 20) or 20)))
            assignments = {}
            for visit, discipline in visit_rows:
                if str(visit.source or "").strip().lower() == "repeat":
                    continue
                candidates = [lesson for lesson in lessons if lesson["discipline"] == discipline and visit_matches_lesson(visit.visited_at, lesson, early_minutes=early_minutes, late_minutes=late_minutes)]
                if candidates:
                    assignments[visit.id] = min(candidates, key=lambda item: lesson_visit_distance(visit.visited_at, item))
            for lesson in lessons:
                started = lesson_start(lesson)
                if now_local < started + timedelta(minutes=lesson_duration(lesson)) or not lesson["staff_ids"]:
                    continue
                time_text = lesson["time"]
                occurrence_key = f"{club.id}:{today.isoformat()}:{lesson['discipline']}:{lesson['index']}:{time_text}"
                if await session.scalar(select(MotivationAccrual.id).where(MotivationAccrual.occurrence_key == occurrence_key)):
                    continue
                student_ids = {visit.student_id for visit, discipline in visit_rows if discipline == lesson["discipline"] and assignments.get(visit.id) is lesson}
                rule = _rules_for(lesson, len(student_ids))
                if not rule:
                    continue
                accrual = MotivationAccrual(
                    club_id=club.id, discipline=lesson["discipline"], occurrence_key=occurrence_key,
                    occurrence_date=today, start_time=time_text, student_count=len(student_ids),
                    rule_min_students=int(rule["min_students"]), rule_max_students=int(rule["max_students"]),
                    rate_kopecks=int(rule["rate_kopecks"]), staff_ids=lesson["staff_ids"],
                )
                session.add(accrual)
                staff_rows = (await session.execute(select(ClubStaff).where(ClubStaff.club_id == club.id, ClubStaff.id.in_(lesson["staff_ids"])))).scalars().all()
                created_items.append((club, accrual, staff_rows, now_local))
                added += 1
        await session.commit()
        for club, accrual, staff_rows, created_at in created_items:
            await _notify_accrual(session, club, accrual, staff_rows, created_at)
    return added


async def _legacy_accrue_finished_lessons(now: datetime | None = None) -> int:
    now_local = (now or datetime.now(MOSCOW)).astimezone(MOSCOW)
    today = now_local.date()
    weekday = DAY_KEYS[today.weekday()]
    added = 0
    created_items = []
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
                    accrual = MotivationAccrual(
                        club_id=club.id, discipline=discipline, occurrence_key=occurrence_key,
                        occurrence_date=today, start_time=time_text, student_count=student_count,
                        rule_min_students=int(rule["min_students"]), rule_max_students=int(rule["max_students"]),
                        rate_kopecks=int(rule["rate_kopecks"]), staff_ids=staff_ids,
                    )
                    session.add(accrual)
                    staff_rows = (await session.execute(select(ClubStaff).where(ClubStaff.club_id == club.id, ClubStaff.id.in_(staff_ids)))).scalars().all()
                    created_items.append((club, accrual, staff_rows, now_local))
                    added += 1
        await session.commit()
        # Отправляем отчёты только после фиксации начислений в БД.
        for item in created_items:
            await _notify_accrual(session, item[0], item[1], item[2], item[3])
    return added


async def motivation_accrual_job():
    await accrue_finished_lessons()


def _next_training(club, staff_id: int, after: datetime) -> str | None:
    settings = club.club_settings if isinstance(club.club_settings, dict) else {}
    for offset in range(1, 15):
        day = (after + timedelta(days=offset)).date()
        for lesson in scheduled_lessons(settings, day):
            if staff_id in lesson["staff_ids"]:
                return f"{day.strftime('%d.%m')} в {lesson['time']} · {lesson['discipline']}"
    return None


def _legacy_next_training(club, staff_id: int, after: datetime) -> str | None:
    settings = club.club_settings if isinstance(club.club_settings, dict) else {}
    day_keys = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    for offset in range(1, 15):
        day = (after + timedelta(days=offset)).date()
        key = day_keys[day.weekday()]
        for discipline, block in (settings.get("disciplines", {}) or {}).items():
            for lesson in ((block or {}).get("schedule", {}) or {}).get(key, []) or []:
                ids = lesson.get("coach_staff_ids") or ([lesson.get("coach_staff_id")] if lesson.get("coach_staff_id") else [])
                if staff_id in [int(value) for value in ids]:
                    return f"{day.strftime('%d.%m')} в {str(lesson.get('time', ''))[:5]} · {discipline}"
    return None


async def _notify_accrual(session, club, accrual, staff_rows, now_local):
    bot = bots_dict.get(club.bot_token) if club else None
    if not bot:
        return
    names = {row.id: row.full_name or f"Тренер #{row.id}" for row in staff_rows}
    total = int(accrual.rate_kopecks or 0) * len(accrual.staff_ids or [])
    for staff_id in accrual.staff_ids or []:
        staff = next((row for row in staff_rows if row.id == int(staff_id)), None)
        if not staff or not staff.telegram_id:
            continue
        next_training = _next_training(club, int(staff_id), now_local)
        text = (
            "✅ <b>Тренировка завершена</b>\n\n"
            f"Дисциплина: <b>{accrual.discipline}</b>\n"
            f"Учеников: <b>{accrual.student_count}</b>\n"
            f"Начислено: <b>{accrual.rate_kopecks / 100:.2f} ₽</b>\n"
            f"Следующая тренировка: <b>{next_training or 'не назначена'}</b>"
        )
        try:
            await bot.send_message(int(staff.telegram_id), text, parse_mode="HTML")
        except Exception:
            pass
    if club.owner_id:
        trainers = ", ".join(names.get(int(staff_id), f"#{staff_id}") for staff_id in accrual.staff_ids or [])
        text = (
            "📊 <b>Отчёт по тренировке</b>\n\n"
            f"Дисциплина: <b>{accrual.discipline}</b>\n"
            f"Тренеры: <b>{trainers or 'не назначены'}</b>\n"
            f"Учеников: <b>{accrual.student_count}</b>\n"
            f"Общая сумма: <b>{total / 100:.2f} ₽</b>"
        )
        try:
            await bot.send_message(int(club.owner_id), text, parse_mode="HTML")
        except Exception:
            pass
