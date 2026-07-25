from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Iterable, Any


def _naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if getattr(dt, "tzinfo", None) is not None else dt


def group_completed_sessions(visits: Iterable[Any], timeout_minutes: int, now: datetime | None = None) -> list[dict]:
    """
    Collapse raw visit logs into completed sessions.

    A session is a sequence of check-ins where the gap between adjacent visits is
    not greater than timeout_minutes. The session is considered completed when
    its last check-in is older than the timeout window.
    """
    timeout_delta = timedelta(minutes=max(1, int(timeout_minutes or 0)))
    now_naive = _naive(now) or datetime.utcnow()
    rows = [v for v in visits if getattr(v, "visited_at", None)]
    rows.sort(key=lambda v: _naive(v.visited_at) or datetime.min)

    sessions: list[dict] = []
    current: list[Any] = []

    def flush() -> None:
        nonlocal current
        if not current:
            return
        start_dt = _naive(current[0].visited_at)
        end_dt = _naive(current[-1].visited_at)
        if start_dt is None or end_dt is None:
            current = []
            return
        session_end = end_dt + timeout_delta
        if session_end <= now_naive:
            sessions.append(
                {
                    "started_at": start_dt,
                    "ended_at": session_end,
                    "last_visit_at": end_dt,
                    "visits_count": len(current),
                    "duration_minutes": max(1, int((session_end - start_dt).total_seconds() // 60)),
                    "active": False,
                    "student_id": getattr(current[0], "student_id", None),
                    "student_name": getattr(current[0], "student_name", None),
                }
            )
        current = []

    for visit in rows:
        visit_dt = _naive(visit.visited_at)
        if not current:
            current = [visit]
            continue
        last_dt = _naive(current[-1].visited_at)
        if visit_dt is not None and last_dt is not None and visit_dt - last_dt <= timeout_delta:
            current.append(visit)
        else:
            flush()
            current = [visit]

    flush()
    sessions.sort(key=lambda item: item["ended_at"], reverse=True)
    return sessions


def attach_student_names(visits: Iterable[Any], student_names: dict[int, str]) -> list[Any]:
    """
    Produce lightweight objects with student_name attached for grouping helpers.
    """
    prepared = []
    for visit in visits:
        student_id = getattr(visit, "student_id", None)
        student_name = student_names.get(student_id, "")
        prepared.append(type("_VisitRow", (), {
            "visited_at": getattr(visit, "visited_at", None),
            "student_id": student_id,
            "student_name": student_name,
        })())
    return prepared


def moscow_str(dt: datetime | None, fmt: str = "%d.%m.%Y %H:%M") -> str:
    if not dt:
        return "—"
    dt_naive = _naive(dt)
    return (dt_naive + timedelta(hours=3)).strftime(fmt) if dt_naive else "—"


def payment_status_label(status: str | None) -> str:
    value = (status or "").upper()
    if value in {"CONFIRMED", "SUCCEEDED", "SUCCESS"}:
        return "✅ оплачено"
    if value in {"NEW", "PENDING", "PROCESSING"}:
        return "⏳ ожидает"
    if value in {"FAILED", "REJECTED", "CANCELED", "CANCELLED"}:
        return "❌ отклонено"
    return f"ℹ️ {status or 'неизвестно'}"


def payment_kind_label(entry: Any) -> str:
    if getattr(entry, "lesson_count", None) is not None or getattr(entry, "days_to_add", None) is not None:
        return "Абонемент"
    return "Товары"


def summarize_payment_entry(entry: Any, *, student_name: str | None = None, item_titles: list[str] | None = None) -> str:
    created = moscow_str(getattr(entry, "created_at", None))
    amount = (int(getattr(entry, "amount_kopecks", 0) or 0) / 100)
    amount_text = f"{amount:.0f} ₽"
    kind = payment_kind_label(entry)
    status = payment_status_label(getattr(entry, "status", None))
    tail_parts: list[str] = []
    if student_name:
        tail_parts.append(student_name)
    if item_titles:
        preview = ", ".join(item_titles[:2])
        if len(item_titles) > 2:
            preview += "…"
        tail_parts.append(preview)
    tail = f" — {' · '.join(tail_parts)}" if tail_parts else ""
    return f"• <code>{created}</code> — {kind} — <b>{amount_text}</b> — {status}{tail}"
