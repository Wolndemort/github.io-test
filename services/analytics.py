import io
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List
from zoneinfo import ZoneInfo

import pandas as pd


MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def _utc_naive(value: datetime | None) -> datetime | None:
    """Normalize a DB timestamp to the project's naive-UTC convention."""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def reporting_periods(now: datetime | None = None) -> dict[str, datetime]:
    """Return UTC-naive boundaries for Moscow calendar periods."""
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    local_now = now_utc.astimezone(MOSCOW_TZ)
    local_today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    local_yesterday = local_today - timedelta(days=1)
    local_week = local_today - timedelta(days=local_today.weekday())
    local_month = local_today.replace(day=1)

    def to_db(value: datetime) -> datetime:
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    return {
        "now": now_utc.astimezone(timezone.utc).replace(tzinfo=None),
        "today": to_db(local_today),
        "yesterday": to_db(local_yesterday),
        "week": to_db(local_week),
        "month": to_db(local_month),
        "local_now": local_now.replace(tzinfo=None),
    }


def moscow_date_boundary(value: date | str) -> datetime:
    """Convert a Moscow calendar date to the naive-UTC DB boundary."""
    if isinstance(value, str):
        value = date.fromisoformat(value)
    local_midnight = datetime.combine(value, datetime.min.time(), tzinfo=MOSCOW_TZ)
    return local_midnight.astimezone(timezone.utc).replace(tzinfo=None)


def moscow_weekday(value: datetime | None) -> int | None:
    value = _utc_naive(value)
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).astimezone(MOSCOW_TZ).weekday()


def _balance(student: Any) -> int:
    return int(getattr(student, "balance_lessons", 0) or 0)


def _has_active_pass(student: Any, now: datetime) -> bool:
    expire_date = _utc_naive(getattr(student, "expire_date", None))
    return bool(
        not getattr(student, "is_frozen", 0)
        and expire_date
        and expire_date > now
        and _balance(student) > 0
    )


def calculate_student_metrics(students_models: Iterable[Any], now: datetime | None = None) -> Dict[str, Any]:
    """Single source of truth for athlete/status counters across all reports."""
    students = list(students_models)
    now = _utc_naive(now) or datetime.now(timezone.utc).replace(tzinfo=None)
    active_students = [s for s in students if _has_active_pass(s, now)]
    frozen_students = [s for s in students if bool(getattr(s, "is_frozen", 0))]
    inactive_students = [s for s in students if s not in active_students and s not in frozen_students]
    burning_students = [s for s in active_students if 0 < _balance(s) <= 3]
    sleeping_students = [
        s for s in students
        if not getattr(s, "last_visit", None)
        or _utc_naive(s.last_visit) <= now - timedelta(days=14)
    ]
    parent_ids = {int(s.parent_id) for s in students if getattr(s, "parent_id", None)}
    discipline_counts: dict[str, int] = {}
    for student in students:
        key = getattr(student, "discipline", None) or "boxing"
        discipline_counts[key] = discipline_counts.get(key, 0) + 1

    # 999 is a marker for unlimited, not 999 real lessons.
    finite_lessons = sum(_balance(s) for s in students if _balance(s) < 999)
    return {
        "total_athletes": len(students),
        "total_parents": len(parent_ids),
        "active_passes": len(active_students),
        "active_now_count": len(active_students),
        "frozen_passes": len(frozen_students),
        "inactive_passes": len(inactive_students),
        "burning_passes": len(burning_students),
        "total_lessons_left": finite_lessons,
        "retention_rate": round(((len(active_students) + len(frozen_students)) / len(students)) * 100, 1) if students else 0,
        "active_students": active_students,
        "inactive_students": inactive_students,
        "frozen_students": frozen_students,
        "burning_students": burning_students,
        "sleeping_students": sleeping_students,
        "discipline_counts": discipline_counts,
    }


def _payment_kopecks(payment: Any) -> int:
    return int(getattr(payment, "amount_kopecks", 0) or 0)


def calculate_revenue_periods(payments: Iterable[Any], now: datetime | None = None) -> dict[str, float]:
    """Calculate revenue from confirmed PaymentOrder and CartOrder objects."""
    periods = reporting_periods(now)
    totals = {"today": 0, "week": 0, "month": 0, "all": 0}
    for payment in payments:
        amount = _payment_kopecks(payment)
        created_at = _utc_naive(getattr(payment, "created_at", None))
        totals["all"] += amount
        if created_at is None:
            continue
        if created_at >= periods["month"]:
            totals["month"] += amount
        if created_at >= periods["week"]:
            totals["week"] += amount
        if created_at >= periods["today"]:
            totals["today"] += amount
    return {key: round(value / 100, 2) for key, value in totals.items()}


def calculate_club_metrics(students_models: List[Any], confirmed_payments: List[Any]) -> Dict[str, Any]:
    metrics = calculate_student_metrics(students_models)
    revenue = int(calculate_revenue_periods(confirmed_payments)["all"])
    if not students_models:
        return {"empty": True, "revenue": revenue, **metrics}
    metrics.update({"empty": False, "revenue": revenue})
    return metrics


def _student_export_row(student: Any, now: datetime) -> dict[str, Any]:
    expire_date = _utc_naive(getattr(student, "expire_date", None))
    if getattr(student, "is_frozen", 0):
        status = "Заморожен"
    elif _has_active_pass(student, now):
        status = "Активен"
    else:
        status = "Истек/без абонемента"
    return {
        "ФИО Атлета": getattr(student, "name", None) or "Не указано",
        "Остаток занятий": _balance(student),
        "Статус": status,
        "Дата окончания": expire_date.strftime("%d.%m.%Y") if expire_date else "Не ограничено",
        "Телефон родителя": getattr(student, "parent_phone", None) or "Не указан",
        "Последний визит": _utc_naive(getattr(student, "last_visit", None)).strftime("%d.%m.%Y %H:%M") if getattr(student, "last_visit", None) else "Нет визитов",
    }


def generate_students_excel(students_models: List[Any]) -> io.BytesIO:
    """Export every athlete in the club, including unlimited athletes."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    df = pd.DataFrame([_student_export_row(s, now) for s in students_models])
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Атлеты клуба")
    output.seek(0)
    return output


def _dashboard_student_row(student: Any) -> dict[str, Any]:
    return {
        "name": getattr(student, "name", None) or "Атлет",
        "balance": _balance(student),
        "is_frozen": bool(getattr(student, "is_frozen", 0)),
        "username": getattr(student, "username", None) or getattr(student, "tg_username", None),
        "parent_id": getattr(student, "parent_id", None),
        "phone": getattr(student, "parent_phone", None),
    }


def calculate_admin_dashboard(students_models: List[Any]) -> Dict[str, Any]:
    """Data for the admin dashboard using the same counters as revenue reports."""
    students = list(students_models)
    if not students:
        return {"empty": True, "total_athletes": 0, "total_parents": 0, "active_now_count": 0}
    metrics = calculate_student_metrics(students)
    return {
        "empty": False,
        "total_athletes": metrics["total_athletes"],
        "total_parents": metrics["total_parents"],
        "active_now_count": metrics["active_now_count"],
        "expired_students": [_dashboard_student_row(s) for s in metrics["inactive_students"]],
        "burning_students": [{**_dashboard_student_row(s), "balance": _balance(s)} for s in metrics["burning_students"]],
        "sleeping_students": [_dashboard_student_row(s) for s in metrics["sleeping_students"]],
        "all_athletes": [_dashboard_student_row(s) for s in students],
    }


def calculate_daily_business_report(students_models: List[Any], today_payments: List[Any], yesterday_payments: List[Any], visit_logs: Iterable[Any] | None = None) -> dict:
    """Daily report metrics; caller supplies payments from both payment tables."""
    today_revenue = calculate_revenue_periods(today_payments)["all"]
    yesterday_revenue = calculate_revenue_periods(yesterday_payments)["all"]
    revenue_diff = today_revenue - yesterday_revenue
    revenue_percent = round((revenue_diff / yesterday_revenue) * 100, 1) if yesterday_revenue else (100.0 if today_revenue else 0.0)
    sign = "📈 +" if revenue_diff >= 0 else "📉 "
    metrics = calculate_student_metrics(students_models)
    if not students_models:
        return {"revenue_today": int(today_revenue), "revenue_diff_text": f"{sign}{int(revenue_diff)} ₽ ({revenue_percent}%)", "top_discipline": "Нет данных", "peak_hours": "Нет данных", "total_athletes": 0, "total_parents": 0}

    discipline = metrics["discipline_counts"]
    top_key = max(discipline, key=discipline.get) if discipline else None
    periods = reporting_periods()
    today_local_date = periods["local_now"].date()
    visit_hours = []
    if visit_logs is not None:
        for visit_log in visit_logs:
            visit = _utc_naive(getattr(visit_log, "visited_at", None))
            if visit and visit.replace(tzinfo=timezone.utc).astimezone(MOSCOW_TZ).date() == today_local_date:
                visit_hours.append(visit.replace(tzinfo=timezone.utc).astimezone(MOSCOW_TZ).hour)
    else:
        for student in students_models:
            visit = _utc_naive(getattr(student, "last_visit", None))
            if visit and visit.replace(tzinfo=timezone.utc).astimezone(MOSCOW_TZ).date() == today_local_date:
                visit_hours.append(visit.replace(tzinfo=timezone.utc).astimezone(MOSCOW_TZ).hour)
    peak_hours = ", ".join(f"{hour}:00" for hour in sorted(set(visit_hours))[:2]) if visit_hours else "Нет чекинов сегодня"
    return {
        "revenue_today": int(today_revenue),
        "revenue_diff_text": f"{sign}{int(revenue_diff)} ₽ ({revenue_percent}%)",
        "top_discipline": str(top_key).upper() if top_key else "НЕТ АТЛЕТОВ",
        "peak_hours": peak_hours,
        "total_athletes": metrics["total_athletes"],
        "total_parents": metrics["total_parents"],
    }
