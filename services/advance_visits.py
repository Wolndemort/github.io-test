"""Controlled loyalty credit for visits made after subscription expiry."""
from __future__ import annotations

from datetime import datetime


DEFAULT_MAX_DEBT_DAYS = 15
DEFAULT_MAX_DEBT_LESSONS = 7
MAX_DEBT_DAYS = 3650
MAX_DEBT_LESSONS = 999


def _safe_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _safe_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return default


def advance_config(settings: dict | None) -> dict:
    settings = settings if isinstance(settings, dict) else {}
    limits = settings.get("limits", {})
    limits = limits if isinstance(limits, dict) else {}
    raw = limits.get("advance_visits", {})
    raw = raw if isinstance(raw, dict) else {}
    return {
        "enabled": _safe_bool(raw.get("enabled", False)),
        "max_debt_days": _safe_int(raw.get("max_debt_days"), DEFAULT_MAX_DEBT_DAYS, 0, MAX_DEBT_DAYS),
        "max_debt_lessons": _safe_int(raw.get("max_debt_lessons"), DEFAULT_MAX_DEBT_LESSONS, 0, MAX_DEBT_LESSONS),
    }


def advance_preview(
    student,
    now: datetime,
    settings: dict | None,
    *,
    new_session: bool = True,
    first_advance_at: datetime | None = None,
) -> dict:
    """Return projected debt without mutating the student."""
    config = advance_config(settings)
    current_days = max(0, int(getattr(student, "advance_debt_days", 0) or 0))
    current_lessons = max(0, int(getattr(student, "advance_debt_lessons", 0) or 0))
    expiry = getattr(student, "expire_date", None)
    expiry = expiry.replace(tzinfo=None) if expiry else None
    zero_balance_with_term = expiry is not None and int(getattr(student, "balance_lessons", 0) or 0) <= 0
    debt_candidate = bool(expiry and expiry < now) or zero_balance_with_term
    # Debt days are based on actual debt visits, not on idle time after expiry.
    # The first debt visit counts as day 1; later debt visits extend the period
    # through the date of the latest real debt visit.
    if first_advance_at is not None:
        first_advance_at = first_advance_at.replace(tzinfo=None)
        visit_period_days = max(1, (now.date() - first_advance_at.date()).days + 1)
    else:
        visit_period_days = 1 if current_lessons == 0 and debt_candidate else 0
    projected_days = max(current_days, visit_period_days)
    projected_lessons = current_lessons + (1 if new_session else 0)
    is_advance = bool(config["enabled"] and debt_candidate and new_session)
    return {
        "enabled": config["enabled"],
        "is_advance": is_advance,
        "debt_days": projected_days,
        "debt_lessons": projected_lessons,
        "max_debt_days": config["max_debt_days"],
        "max_debt_lessons": config["max_debt_lessons"],
        "allowed": (not is_advance) or (projected_days <= config["max_debt_days"] and projected_lessons <= config["max_debt_lessons"]),
    }


def record_advance_visit(student, now: datetime, settings: dict | None) -> dict:
    preview = advance_preview(student, now, settings, new_session=True)
    if preview["is_advance"] and preview["allowed"]:
        student.advance_debt_days = preview["debt_days"]
        student.advance_debt_lessons = preview["debt_lessons"]
    return preview


def settle_advance_debt(student, days_to_add: int, lessons_count: int, settings: dict | None) -> dict:
    """Consume debt from a newly purchased subscription while holding the student lock."""
    debt_days = max(0, int(getattr(student, "advance_debt_days", 0) or 0))
    debt_lessons = max(0, int(getattr(student, "advance_debt_lessons", 0) or 0))
    # Once debt exists it is a real accounting obligation. It must be settled
    # even if the club later disables creation of new advance visits.
    days_consumed = min(debt_days, max(0, int(days_to_add or 0)))
    lessons_consumed = debt_lessons if int(lessons_count or 0) == 999 else min(debt_lessons, max(0, int(lessons_count or 0)))
    student.advance_debt_days = debt_days - days_consumed
    student.advance_debt_lessons = debt_lessons - lessons_consumed
    return {
        "debt_days_before": debt_days,
        "debt_lessons_before": debt_lessons,
        "days_consumed": days_consumed,
        "lessons_consumed": lessons_consumed,
        "debt_days_after": student.advance_debt_days,
        "debt_lessons_after": student.advance_debt_lessons,
    }


def advance_settlement_notice(settlement: dict | None, new_expire=None) -> str:
    """Build the post-purchase alert from the actual settlement result."""
    if not settlement or not settlement.get("debt_days_before", 0) and not settlement.get("debt_lessons_before", 0):
        return ""
    consumed_days = int(settlement.get("days_consumed", 0) or 0)
    consumed_lessons = int(settlement.get("lessons_consumed", 0) or 0)
    left_days = int(settlement.get("debt_days_after", 0) or 0)
    left_lessons = int(settlement.get("debt_lessons_after", 0) or 0)
    lines = [
        "✅ <b>Авансовый долг перерасчитан</b>",
        f"Списано из нового абонемента: <b>{consumed_lessons}</b> занятий и <b>{consumed_days}</b> дн.",
        f"Осталось долга: <b>{left_lessons}</b> занятий и <b>{left_days}</b> дн.",
    ]
    if new_expire:
        lines.append(f"Новый срок абонемента: <b>{new_expire}</b>")
    return "\n".join(lines)


def advance_debt_snapshot(student) -> dict:
    """Return a stable, serialisable debt snapshot for UI and notifications."""
    return {
        "days": max(0, int(getattr(student, "advance_debt_days", 0) or 0)),
        "lessons": max(0, int(getattr(student, "advance_debt_lessons", 0) or 0)),
    }


def advance_profile_notice(student) -> str:
    """Compact client-facing debt status; empty until debt actually exists."""
    debt_days = max(0, int(getattr(student, "advance_debt_days", 0) or 0))
    debt_lessons = max(0, int(getattr(student, "advance_debt_lessons", 0) or 0))
    if debt_days <= 0 and debt_lessons <= 0:
        return ""
    lesson_word = "занятие" if debt_lessons == 1 else "занятия" if debt_lessons in {2, 3, 4} else "занятий"
    day_word = "день" if debt_days == 1 else "дня" if debt_days in {2, 3, 4} else "дней"
    lesson_text = (
        f"тариф с занятиями — спишется <b>{debt_lessons}</b> "
        f"{lesson_word}"
        if debt_lessons > 0 else "тариф с занятиями — долг не сформирован"
    )
    unlimited_text = (
        f"безлимит — учтётся <b>{debt_days}</b> "
        f"{day_word} срока"
        if debt_days > 0 else "безлимит — срок не уменьшится"
    )
    return (
        f"⚠️ <b>Посещения в долг: {debt_lessons}</b>\n"
        f"При покупке нового абонемента: {lesson_text}; {unlimited_text}."
    )


def advance_purchase_warning(student, settings: dict | None, *, html: bool = False) -> str:
    """Explain to a payer that the next purchase will settle an advance debt."""
    debt = advance_debt_snapshot(student)
    if not debt["days"] and not debt["lessons"]:
        return ""
    text = (
        f"⚠️ У атлета есть задолженность по авансовым посещениям: "
        f"{debt['days']} дн. / {debt['lessons']} зан. "
        "При покупке она будет погашена из нового абонемента "
        "(срок и количество занятий уменьшатся)."
    )
    if html:
        from html import escape
        return escape(text)
    return text
