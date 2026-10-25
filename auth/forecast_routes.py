import os
import uuid
import base64
import hashlib
import hmac
import io
from datetime import date, datetime, timedelta
import re
import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import AuditEntry, CartOrder, CartItem, CashEntry, Club, ClubProduct, ClubStaff, Discount, DiscountAssignment, PaymentOrder, Subscription, Student, StudentParent, VisitLog, User, add_abon, process_student_freeze, purchase_student_freeze, get_session
from services.input_normalization import normalize_ru_phone
from services.audit import audit_event
from services.schedule_utils import normalize_schedule_block
from services.gate_control import process_athlete_gate_pass
from handlers.skud import trigger_dingtian_turnstile
from services.discounts import active_discounts, apply_discounts
from services.yookassa_client import YooKassaClient
from services.broadcast import broadcast_recipients_for_student_search, resolve_broadcast_recipients
from services.club_limits import LIMIT_DEFAULTS, normalize_limit_updates
from config import PROXY_URL, secret_key
from auth.native_auth import deliver_email_broadcast
import qrcode
from services.analytics import (
    build_expiry_series,
    build_revenue_series,
    build_visit_series,
    calculate_projected_renewal_revenue,
    reporting_periods,
    calculate_revenue_periods,
    calculate_cash_flow_periods,
    calculate_student_metrics,
)
from services.analytics import generate_students_excel
from services.analytics import calculate_admin_dashboard
from services.bot_registry import bots_dict
from .context import AuthContext
from .routes import web_context
from .web_session import require_csrf, require_web_context

router = APIRouter(prefix="/api/v1/staff/forecast", tags=["Web Forecast"])
web_router = APIRouter(tags=["Web Forecast"])
revenue_router = APIRouter(prefix="/api/v1/staff/revenue", tags=["Web Revenue"])
students_router = APIRouter(prefix="/api/v1/staff/students", tags=["Web Students"])
overview_router = APIRouter(prefix="/api/v1/staff/overview", tags=["Web Overview"])
cash_router = APIRouter(prefix="/api/v1/staff/cash", tags=["Web Cash"])
sales_router = APIRouter(prefix="/api/v1/staff/sales", tags=["Web Sales"])
audit_router = APIRouter(prefix="/api/v1/staff/audit", tags=["Web Audit"])
schedule_router = APIRouter(prefix="/api/v1/staff/schedule", tags=["Web Schedule"])
catalog_router = APIRouter(prefix="/api/v1/staff/catalog", tags=["Web Catalog"])
client_router = APIRouter(prefix="/api/v1/client", tags=["Web Client"])
settings_router = APIRouter(prefix="/api/v1/staff/settings", tags=["Web Settings"])
checkin_router = APIRouter(prefix="/api/v1/staff/checkin", tags=["Web Checkin"])
freeze_router = APIRouter(prefix="/api/v1/staff/freeze", tags=["Web Freeze"])

async def client_scoped_students(actor: AuthContext, session: AsyncSession):
    result = await session.execute(
        select(Student).outerjoin(StudentParent, StudentParent.student_id == Student.id).where(
            Student.club_id == actor.club_id,
            or_(Student.parent_id == actor.user_id, StudentParent.parent_id == actor.user_id),
        ).distinct().order_by(Student.name)
    )
    return list(result.scalars().all())

@router.get("/broadcast/recipients")
async def web_broadcast_recipients(
    request: Request,
    q: str = Query(default="", max_length=100),
    context: AuthContext | None = Depends(web_context),
    session: AsyncSession = Depends(get_session),
):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "broadcast" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    if actor.actor_type not in {"owner", "staff"}:
        raise HTTPException(status_code=403, detail={"code": "staff_access_required"})
    rows = await broadcast_recipients_for_student_search(session, actor.club_id, q)
    club = await session.scalar(select(Club).where(Club.id == actor.club_id))
    settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}
    configured = settings.get("disciplines", {}) if isinstance(settings.get("disciplines", {}), dict) else {}
    disciplines = [{"value": code, "name": value.get("name") or code} for code, value in configured.items() if isinstance(value, dict)]
    known = {item["value"] for item in disciplines}
    disciplines.extend({"value": row["discipline"], "name": row["discipline"]} for row in rows if row.get("discipline") and row["discipline"] not in known)
    return {"club_id": actor.club_id, "students": rows, "disciplines": disciplines, "read_only": True}


@router.post("/broadcast")
async def web_broadcast(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "broadcast" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    if actor.actor_type not in {"owner", "staff"}:
        raise HTTPException(status_code=403, detail={"code": "staff_access_required"})
    await require_csrf(request.app.state.redis_client, request)
    payload = await request.json()
    text = str(payload.get("text") or "").strip()
    key = str(payload.get("idempotency_key") or "").strip()
    selected_ids = payload.get("student_ids")
    if selected_ids is not None and (
        not isinstance(selected_ids, list)
        or len(selected_ids) > 1000
        or any(isinstance(item, bool) or not isinstance(item, int) for item in selected_ids)
    ):
        raise HTTPException(status_code=400, detail={"code": "invalid_broadcast_recipients"})
    if not 1 <= len(text) <= 4096 or not key or len(key) > 100:
        raise HTTPException(status_code=400, detail={"code": "invalid_broadcast"})
    club = await session.scalar(select(Club).where(Club.id == actor.club_id))
    settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}
    if not settings.get("features", {}).get("broadcast", True):
        raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if not club or not club.bot_token or club.bot_token not in bots_dict:
        raise HTTPException(status_code=503, detail={"code": "club_bot_unavailable"})
    if not await request.app.state.redis_client.set(f"web:broadcast:{actor.club_id}:{key}", "1", ex=900, nx=True):
        return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id}
    user_ids = await resolve_broadcast_recipients(session, actor.club_id, selected_ids)
    if selected_ids is not None and not user_ids:
        raise HTTPException(status_code=400, detail={"code": "broadcast_recipients_not_found"})
    bot = bots_dict[club.bot_token]
    delivered = 0
    failed = 0
    for user_id in sorted(user_ids):
        try:
            await bot.send_message(chat_id=user_id, text=text)
            delivered += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    audit_event("web_broadcast_sent", club_id=actor.club_id, actor_user_id=actor.user_id, actor_role=actor.role, action="broadcast", object_type="club", object_id=actor.club_id, location="web/staff/broadcast", recipients=len(user_ids), delivered=delivered, failed=failed, selected_student_ids=selected_ids)
    return {"ok": True, "club_id": actor.club_id, "recipients": len(user_ids), "delivered": delivered, "failed": failed, "selected": selected_ids is not None}

@router.post("/broadcast/email")
async def web_email_broadcast(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "broadcast" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    if actor.actor_type not in {"owner", "staff"}: raise HTTPException(status_code=403, detail={"code": "staff_access_required"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json(); subject = str(payload.get("subject") or "").strip(); text = str(payload.get("text") or "").strip(); key = str(payload.get("idempotency_key") or "").strip()
    if not 1 <= len(subject) <= 200 or not 1 <= len(text) <= 10000 or not key or len(key) > 100: raise HTTPException(status_code=400, detail={"code": "invalid_email_broadcast"})
    if not await request.app.state.redis_client.set(f"web:email-broadcast:{actor.club_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id}
    users = list((await session.scalars(select(User).where(User.club_id == actor.club_id, User.email.is_not(None)))).all()); emails = sorted({str(user.email).strip().lower() for user in users if user.email and "@" in str(user.email)})
    delivered = failed = 0
    for email in emails:
        try: await deliver_email_broadcast(email, subject, text); delivered += 1; await asyncio.sleep(0.05)
        except Exception: failed += 1
    audit_event("web_email_broadcast_sent", club_id=actor.club_id, actor_user_id=actor.user_id, actor_role=actor.role, action="broadcast", object_type="club", object_id=actor.club_id, location="web/staff/broadcast/email", recipients=len(emails), delivered=delivered, failed=failed)
    return {"ok": True, "club_id": actor.club_id, "recipients": len(emails), "delivered": delivered, "failed": failed}

@sales_router.get("/buyers")
async def sale_buyers_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "cash_sale" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    buyers = list((await session.execute(select(User).where(User.club_id == actor.club_id).order_by(User.full_name, User.user_id).limit(200))).scalars().all())
    return {"club_id": actor.club_id, "buyers": [{"id": user.user_id, "name": user.full_name or f"User {user.user_id}"} for user in buyers], "read_only": True}

@sales_router.get("/options")
async def sale_options_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "cash_sale" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    students = list((await session.execute(select(Student).where(Student.club_id == actor.club_id).order_by(Student.name).limit(200))).scalars().all())
    club = await session.scalar(select(Club).where(Club.id == actor.club_id))
    settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}
    disciplines = settings.get("disciplines", {}) if isinstance(settings.get("disciplines", {}), dict) else {}
    return {"club_id": actor.club_id, "students": [{"id": s.id, "name": s.name, "discipline": s.discipline} for s in students], "tariffs": {name: block.get("tariffs", []) for name, block in disciplines.items() if isinstance(block, dict)}, "read_only": True}


def _date_range(value: str | None, fallback: date) -> tuple[date, date]:
    start = date.fromisoformat(value) if value else fallback
    return start, start + timedelta(days=60)

def _age_on(birthday: date | None, today: date) -> int | None:
    if not birthday:
        return None
    return today.year - birthday.year - ((today.month, today.day) < (birthday.month, birthday.day))

def _registration_series(students, start: date, finish: date) -> list[dict]:
    buckets = {}
    cursor = start
    while cursor <= finish:
        buckets[cursor.isoformat()] = 0
        cursor += timedelta(days=1)
    for student in students:
        created = getattr(student, "created_at", None)
        if created:
            day = created.date().isoformat()
            if day in buckets:
                buckets[day] += 1
    return [{"date": day, "count": count} for day, count in buckets.items()]


def build_forecast_payload(*, club, students, visits, payments, cart_orders, cash_entries, start: date, finish: date, revenue_start: date, revenue_finish: date, visits_start: date, visits_finish: date) -> dict:
    periods = reporting_periods()
    now = periods["now"]
    forecast = calculate_projected_renewal_revenue(students, visits, payments, club.club_settings or {}, start.isoformat(), finish.isoformat(), now)
    visit_map = {}
    for visit in visits:
        if visit.student_id not in visit_map or visit.visited_at > visit_map[visit.student_id]:
            visit_map[visit.student_id] = visit.visited_at
    rows = []
    for student in forecast["students"]:
        last = visit_map.get(student.id, student.last_visit)
        rows.append({"name": student.name, "student_id": student.id, "expire_date": student.expire_date.date().isoformat() if student.expire_date else None, "last_visit": last.isoformat() if last else None, "discipline": student.discipline or "—"})
    expiry = build_expiry_series([{**row, "expire_date": date.fromisoformat(row["expire_date"]) if row["expire_date"] else None} for row in rows], start, finish)
    revenue = build_revenue_series(payments, cart_orders, cash_entries, revenue_start, revenue_finish, periods["local_now"].date())
    visits_chart = build_visit_series(visits, visits_start, visits_finish)
    registrations = _registration_series(students, revenue_start, revenue_finish)
    return {"club_id": club.id, "forecast": {"students": rows, "discipline_counts": forecast.get("discipline_counts", {}), "expiry_series": expiry["series"], "revenue_series": revenue["series"], "visit_series": visits_chart["series"], "registration_series": registrations, "peak_expiry_count": expiry["peak_count"], "peak_revenue_amount": revenue["peak_amount"], "peak_visit_count": visits_chart["peak_count"], "registration_total": sum(item["count"] for item in registrations)}, "ranges": {"expiry": [start.isoformat(), finish.isoformat()], "revenue": [revenue_start.isoformat(), revenue_finish.isoformat()], "visits": [visits_start.isoformat(), visits_finish.isoformat()], "registrations": [revenue_start.isoformat(), revenue_finish.isoformat()]}, "read_only": True}


@router.get("/data")
async def forecast_data(
    request: Request,
    context: AuthContext | None = Depends(web_context),
    session: AsyncSession = Depends(get_session),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    revenue_from: str | None = Query(default=None),
    revenue_to: str | None = Query(default=None),
    visits_from: str | None = Query(default=None),
    visits_to: str | None = Query(default=None),
):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "forecast_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра прогноза"})
    club = await session.scalar(select(Club).where(Club.id == actor.club_id))
    if not club:
        raise HTTPException(status_code=404, detail={"code": "club_not_found", "message": "Клуб не найден"})
    today = reporting_periods()["local_now"].date()
    start = date.fromisoformat(date_from) if date_from else today - timedelta(days=30)
    finish = date.fromisoformat(date_to) if date_to else today + timedelta(days=30)
    if finish < start or finish - start > timedelta(days=366):
        raise HTTPException(status_code=400, detail={"code": "invalid_date_range", "message": "Период должен быть от 0 до 366 дней"})
    revenue_start = date.fromisoformat(revenue_from) if revenue_from else start
    revenue_finish = date.fromisoformat(revenue_to) if revenue_to else finish
    visits_start = date.fromisoformat(visits_from) if visits_from else start
    visits_finish = date.fromisoformat(visits_to) if visits_to else finish
    students = list((await session.execute(select(Student).where(Student.club_id == actor.club_id))).scalars().all())
    visits = list((await session.execute(select(VisitLog).where(VisitLog.club_id == actor.club_id))).scalars().all())
    payments = list((await session.execute(select(PaymentOrder).where(PaymentOrder.club_id == actor.club_id, PaymentOrder.status == "CONFIRMED"))).scalars().all())
    cart_orders = list((await session.execute(select(CartOrder).where(CartOrder.club_id == actor.club_id, CartOrder.status == "CONFIRMED"))).scalars().all())
    cash_entries = list((await session.execute(select(CashEntry).where(CashEntry.club_id == actor.club_id))).scalars().all())
    return build_forecast_payload(club=club, students=students, visits=visits, payments=payments, cart_orders=cart_orders, cash_entries=cash_entries, start=start, finish=finish, revenue_start=revenue_start, revenue_finish=revenue_finish, visits_start=visits_start, visits_finish=visits_finish)

@router.get("/age-groups")
async def forecast_age_groups(
    request: Request,
    age_group: str = Query(..., pattern=r"^(1-10|11-15|16\+)$"),
    context: AuthContext | None = Depends(web_context),
    session: AsyncSession = Depends(get_session),
):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "forecast_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    limits = {"1-10": (1, 10), "11-15": (11, 15), "16+": (16, 150)}[age_group]
    today = reporting_periods()["local_now"].date()
    students = list((await session.execute(select(Student).where(Student.club_id == actor.club_id).order_by(Student.name))).scalars().all())
    rows = []
    for student in students:
        age = _age_on(student.birthday, today)
        if age is None or not limits[0] <= age <= limits[1]:
            continue
        rows.append({"student_id": student.id, "name": student.name, "age": age, "discipline": student.discipline or "—", "subscription": "Активен" if student.expire_date and student.expire_date.date() >= today else "Нет активного абонемента", "expire_date": student.expire_date.date().isoformat() if student.expire_date else None})
    return {"club_id": actor.club_id, "age_group": age_group, "students": rows, "read_only": True}


@revenue_router.get("/data")
async def revenue_data(
    request: Request,
    context: AuthContext | None = Depends(web_context),
    session: AsyncSession = Depends(get_session),
    date_from: str | None = Query(default=None, max_length=10),
    date_to: str | None = Query(default=None, max_length=10),
):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and not ({"analytics_view", "freeze_manage"} & set(actor.permissions)):
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра выручки"})
    payments = list((await session.execute(select(PaymentOrder).where(PaymentOrder.club_id == actor.club_id, PaymentOrder.status == "CONFIRMED"))).scalars().all())
    cart_orders = list((await session.execute(select(CartOrder).where(CartOrder.club_id == actor.club_id, CartOrder.status == "CONFIRMED"))).scalars().all())
    date_from_value = date_from if isinstance(date_from, str) else None
    date_to_value = date_to if isinstance(date_to, str) else None
    try:
        start = date.fromisoformat(date_from_value) if date_from_value else None
        finish = date.fromisoformat(date_to_value) if date_to_value else None
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": "invalid_date_range"})
    if start and finish and start > finish:
        raise HTTPException(status_code=400, detail={"code": "invalid_date_range"})
    if start or finish:
        def in_range(item):
            created = getattr(item, "created_at", None)
            day = created.date() if created else None
            return day is not None and (not start or day >= start) and (not finish or day <= finish)
        payments = [item for item in payments if in_range(item)]
        cart_orders = [item for item in cart_orders if in_range(item)]
    result = {"club_id": actor.club_id, "totals": calculate_revenue_periods(payments + cart_orders), "read_only": True}
    if date_from_value or date_to_value:
        result["filters"] = {"date_from": date_from_value or "", "date_to": date_to_value or ""}
    return result


@students_router.get("/data")
async def students_data(
    request: Request,
    context: AuthContext | None = Depends(web_context),
    session: AsyncSession = Depends(get_session),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None, max_length=20),
    discipline: str | None = Query(default=None, max_length=50),
    full: bool = Query(default=False),
    age_min: int | None = Query(default=None, ge=0, le=120),
    age_max: int | None = Query(default=None, ge=0, le=120),
    missing_birthday: bool = Query(default=False),
    no_parent: bool = Query(default=False),
):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра клиентов"})
    query = select(Student).where(Student.club_id == actor.club_id)
    q_value = q if isinstance(q, str) else ""
    if q_value.strip():
        query = query.where(Student.name.ilike(f"%{q_value.strip()}%"))
    def _query_value(value, fallback=None):
        return getattr(value, "default", fallback) if not isinstance(value, (str, bool, int, type(None))) else value
    status_value = _query_value(status, "") or ""; discipline_value = _query_value(discipline, "") or ""
    age_min_value = _query_value(age_min); age_max_value = _query_value(age_max)
    missing_birthday_value = bool(_query_value(missing_birthday, False)); no_parent_value = bool(_query_value(no_parent, False))
    if discipline_value.strip(): query = query.where(Student.discipline == discipline_value.strip())
    students = list((await session.execute(query.order_by(Student.name).offset(offset).limit(limit))).scalars().all())
    if status_value == "frozen": students = [student for student in students if bool(student.is_frozen)]
    elif status_value == "active": students = [student for student in students if bool(student.expire_date and student.balance_lessons and not student.is_frozen)]
    elif status_value == "inactive": students = [student for student in students if not (student.expire_date and student.balance_lessons and not student.is_frozen)]
    today = date.today()
    def student_age(student):
        birthday = getattr(student, "birthday", None)
        if not birthday: return None
        return today.year - birthday.year - ((today.month, today.day) < (birthday.month, birthday.day))
    if age_min_value is not None: students = [student for student in students if (student_age(student) is not None and student_age(student) >= int(age_min_value))]
    if age_max_value is not None: students = [student for student in students if (student_age(student) is not None and student_age(student) <= int(age_max_value))]
    if missing_birthday_value: students = [student for student in students if not getattr(student, "birthday", None)]
    if no_parent_value: students = [student for student in students if not getattr(student, "parent_id", None)]
    visit_map = {}
    for visit in (await session.execute(select(VisitLog).where(VisitLog.club_id == actor.club_id))).scalars().all():
        at = getattr(visit, "visited_at", None)
        if at and (getattr(visit, "student_id", None) not in visit_map or at > visit_map[getattr(visit, "student_id", None)]): visit_map[getattr(visit, "student_id", None)] = at
    rows = [{"id": student.id, "name": student.name, "discipline": student.discipline, "parent_id": getattr(student, "parent_id", None)} for student in students]
    full_value = full if isinstance(full, bool) else False
    if full_value:
        for row, student in zip(rows, students):
            birthday = getattr(student, "birthday", None); last_visit = visit_map.get(student.id, getattr(student, "last_visit", None))
            row.update({"expire_date": getattr(student, "expire_date", None).isoformat() if getattr(student, "expire_date", None) else None, "balance_lessons": getattr(student, "balance_lessons", 0) or 0, "is_frozen": bool(getattr(student, "is_frozen", 0)), "has_subscription": bool(getattr(student, "expire_date", None) and getattr(student, "balance_lessons", 0)), "birthday": birthday.isoformat() if birthday else None, "age": student_age(student), "last_visit": last_visit.isoformat() if last_visit else None, "parent_phone": getattr(student, "parent_phone", None), "parent_phone_secondary": getattr(student, "parent_phone_secondary", None), "email": getattr(student, "email", None), "can_freeze": getattr(student, "can_freeze", 0) or 0, "freeze_days_entitlement": getattr(student, "freeze_days_entitlement", None), "frozen_days": getattr(student, "frozen_days", None), "comment": getattr(student, "comment", None) or ""})
    pagination = {"limit": limit, "offset": offset, "returned": len(students), "query": q_value}
    if status_value or discipline_value or full_value or age_min_value is not None or age_max_value is not None or missing_birthday_value or no_parent_value: pagination.update({"status": status_value, "discipline": discipline_value, "age_min": age_min_value, "age_max": age_max_value, "missing_birthday": missing_birthday_value, "no_parent": no_parent_value})
    return {"club_id": actor.club_id, "students": rows, "pagination": pagination, "read_only": True}


@students_router.post("")
async def create_student_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type not in {"owner", "staff"} or (actor.actor_type == "staff" and "student_manage" not in actor.permissions):
        raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request)
    payload = await request.json(); key = str(payload.get("idempotency_key") or "").strip(); name = str(payload.get("name") or "").strip(); discipline = str(payload.get("discipline") or "").strip()
    if not key or len(key) > 100: raise HTTPException(status_code=400, detail={"code": "invalid_idempotency_key"})
    if not await request.app.state.redis_client.set(f"web:create-student:{actor.club_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True}
    if not 2 <= len(name) <= 120 or not 1 <= len(discipline) <= 50: raise HTTPException(status_code=400, detail={"code": "invalid_student_fields"})
    club = await session.scalar(select(Club).where(Club.id == actor.club_id)); configured = (club.club_settings or {}).get("disciplines", {}) if club else {}
    if discipline not in configured: raise HTTPException(status_code=400, detail={"code": "discipline_not_found"})
    birthday = None
    if payload.get("birthday"):
        try: birthday = date.fromisoformat(str(payload["birthday"]))
        except ValueError: raise HTTPException(status_code=400, detail={"code": "invalid_birthday"})
    email = str(payload.get("email") or "").strip().lower()
    if email and (len(email) > 320 or "@" not in email): raise HTTPException(status_code=400, detail={"code": "invalid_email"})
    student = Student(club_id=actor.club_id, name=name, discipline=discipline, birthday=birthday, email=email or None, parent_phone=normalize_ru_phone(str(payload.get("parent_phone") or "")) or None, parent_phone_secondary=normalize_ru_phone(str(payload.get("parent_phone_secondary") or "")) or None, comment=str(payload.get("comment") or "")[:1000] or None)
    session.add(student); await session.commit(); audit_event("web_student_created", club_id=actor.club_id, actor_user_id=actor.user_id, action="create", object_type="student", object_id=student.id, location="web/staff/students")
    return {"ok": True, "student_id": student.id}

@students_router.delete("/{student_id}")
async def delete_student_data(student_id: int, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type not in {"owner", "staff"} or (actor.actor_type == "staff" and "student_manage" not in actor.permissions): raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request)
    payload = await request.json(); key = str(payload.get("idempotency_key") or "").strip()
    if not key or len(key) > 100: raise HTTPException(status_code=400, detail={"code": "invalid_idempotency_key"})
    if not await request.app.state.redis_client.set(f"web:delete-student:{actor.club_id}:{student_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True}
    student = await session.scalar(select(Student).where(Student.id == student_id, Student.club_id == actor.club_id).with_for_update())
    if not student: raise HTTPException(status_code=404, detail={"code": "student_not_found"})
    await session.delete(student); await session.commit(); audit_event("web_student_deleted", club_id=actor.club_id, actor_user_id=actor.user_id, action="delete", object_type="student", object_id=student_id, location="web/staff/students")
    return {"ok": True, "student_id": student_id}

@students_router.post("/bulk/invitations")
async def bulk_student_invitations(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "student_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    if actor.actor_type not in {"owner", "staff"}: raise HTTPException(status_code=403, detail={"code": "staff_access_required"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json(); ids = payload.get("student_ids"); slot = int(payload.get("parent_slot") or 1); key = str(payload.get("idempotency_key") or "").strip()
    if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or slot not in {1, 2} or not key or len(key) > 100: raise HTTPException(status_code=400, detail={"code": "invalid_bulk_invitation"})
    if not await request.app.state.redis_client.set(f"web:bulk-invitations:{actor.club_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True}
    club = await session.scalar(select(Club).where(Club.id == actor.club_id)); username = str((club.club_settings or {}).get("bot_username") or "speedycrm_bot") if club else "speedycrm_bot"; students = list((await session.execute(select(Student).where(Student.club_id == actor.club_id, Student.id.in_([int(x) for x in ids])))).scalars().all())
    invitations = [{"student_id": s.id, "url": f"https://t.me/{username}?start=invite_{s.id}_{slot}", "has_phone": bool((s.parent_phone if slot == 1 else s.parent_phone_secondary))} for s in students]; audit_event("web_bulk_invitations_created", club_id=actor.club_id, actor_user_id=actor.user_id, action="invite", object_type="students", object_id=actor.club_id, location="web/staff/invitations", count=len(invitations), parent_slot=slot)
    return {"ok": True, "club_id": actor.club_id, "parent_slot": slot, "invitations": invitations, "read_only": False}

@students_router.get("/{student_id}")
async def student_detail_data(student_id: int, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра клиентов"})
    student = await session.scalar(select(Student).where(Student.id == student_id, Student.club_id == actor.club_id))
    if not student:
        raise HTTPException(status_code=404, detail={"code": "student_not_found", "message": "Клиент не найден"})
    return {"club_id": actor.club_id, "student": {"id": student.id, "name": student.name, "discipline": student.discipline, "expire_date": student.expire_date.isoformat() if student.expire_date else None, "balance_lessons": student.balance_lessons or 0, "is_frozen": bool(student.is_frozen)}, "read_only": True}


@students_router.patch("/{student_id}")
async def update_student_data(student_id: int, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_STUDENT_MUTATIONS_ENABLED", "0") != "1":
        raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request)
    payload = await request.json()
    key = str(payload.get("idempotency_key") or "").strip()
    if not key or len(key) > 100:
        raise HTTPException(status_code=400, detail={"code": "invalid_idempotency_key"})
    accepted = await request.app.state.redis_client.set(f"web:update-student:{actor.club_id}:{student_id}:{key}", "1", ex=300, nx=True)
    if not accepted:
        return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id, "student_id": student_id}
    student = await session.scalar(select(Student).where(Student.id == student_id, Student.club_id == actor.club_id).with_for_update())
    if not student:
        raise HTTPException(status_code=404, detail={"code": "student_not_found"})
    if actor.actor_type == "client" and student.parent_id != actor.user_id:
        linked = await session.scalar(select(StudentParent).where(StudentParent.student_id == student.id, StudentParent.parent_id == actor.user_id))
        if not linked:
            raise HTTPException(status_code=403, detail={"code": "student_scope_denied"})
    allowed = {"name", "discipline", "parent_phone", "parent_phone_secondary", "email", "birthday", "balance_lessons", "expire_date", "can_freeze", "frozen_days", "comment", "is_frozen"}
    if set(payload) - allowed - {"idempotency_key"}:
        raise HTTPException(status_code=400, detail={"code": "invalid_student_fields"})
    if "name" in payload:
        name = str(payload["name"] or "").strip()
        if not 2 <= len(name) <= 120:
            raise HTTPException(status_code=400, detail={"code": "invalid_student_name"})
        student.name = name
    if "discipline" in payload:
        discipline = str(payload["discipline"] or "").strip()
        if not 1 <= len(discipline) <= 50:
            raise HTTPException(status_code=400, detail={"code": "invalid_student_discipline"})
        student.discipline = discipline
    if "email" in payload:
        email = str(payload["email"] or "").strip().lower()
        if email and (len(email) > 320 or "@" not in email): raise HTTPException(status_code=400, detail={"code": "invalid_email"})
        student.email = email or None
    for field in ("parent_phone", "parent_phone_secondary"):
        if field in payload:
            value = str(payload[field] or "").strip()
            setattr(student, field, normalize_ru_phone(value) if value else None)
    if "birthday" in payload:
        value = str(payload["birthday"] or "").strip()
        try: student.birthday = date.fromisoformat(value) if value else None
        except ValueError: raise HTTPException(status_code=400, detail={"code": "invalid_birthday"})
    if "expire_date" in payload:
        value = str(payload["expire_date"] or "").strip()
        try: student.expire_date = date.fromisoformat(value) if value else None
        except ValueError: raise HTTPException(status_code=400, detail={"code": "invalid_expire_date"})
    if "balance_lessons" in payload: student.balance_lessons = max(0, min(999, int(payload["balance_lessons"] or 0)))
    if "can_freeze" in payload: student.can_freeze = max(0, min(99, int(payload["can_freeze"] or 0)))
    if "frozen_days" in payload: student.frozen_days = int(payload["frozen_days"]) if payload["frozen_days"] else None
    if "comment" in payload: student.comment = str(payload["comment"] or "")[:1000] or None
    if "is_frozen" in payload: student.is_frozen = 1 if payload["is_frozen"] else 0
    await session.commit()
    audit_event("web_student_updated", club_id=actor.club_id, actor_user_id=actor.user_id, action="update", object_type="student", object_id=student_id, location="web/staff/students")
    return {"ok": True, "club_id": actor.club_id, "student_id": student_id, "read_only": False}


@students_router.get("/{student_id}/visits")
async def student_visits_data(student_id: int, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session), limit: int = Query(default=50, ge=1, le=100)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра посещений"})
    student = await session.scalar(select(Student).where(Student.id == student_id, Student.club_id == actor.club_id))
    if not student:
        raise HTTPException(status_code=404, detail={"code": "student_not_found", "message": "Клиент не найден"})
    visits = list((await session.execute(select(VisitLog).where(VisitLog.student_id == student_id, VisitLog.club_id == actor.club_id).order_by(VisitLog.visited_at.desc()).limit(limit))).scalars().all())
    return {"club_id": actor.club_id, "student_id": student_id, "visits": [{"visited_at": v.visited_at.isoformat() if v.visited_at else None, "source": v.source} for v in visits], "read_only": True}


@students_router.get("/{student_id}/payments")
async def student_payments_data(student_id: int, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session), limit: int = Query(default=50, ge=1, le=100)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра платежей"})
    student = await session.scalar(select(Student).where(Student.id == student_id, Student.club_id == actor.club_id))
    if not student:
        raise HTTPException(status_code=404, detail={"code": "student_not_found", "message": "Клиент не найден"})
    payments = list((await session.execute(select(PaymentOrder).where(PaymentOrder.student_id == student_id, PaymentOrder.club_id == actor.club_id, PaymentOrder.status == "CONFIRMED").order_by(PaymentOrder.created_at.desc()).limit(limit))).scalars().all())
    return {"club_id": actor.club_id, "student_id": student_id, "payments": [{"amount_kopecks": p.amount_kopecks, "created_at": p.created_at.isoformat() if p.created_at else None, "type": p.type} for p in payments], "read_only": True}


@students_router.get("/{student_id}/discounts")
async def student_discounts_data(student_id: int, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра скидок"})
    student = await session.scalar(select(Student).where(Student.id == student_id, Student.club_id == actor.club_id))
    if not student:
        raise HTTPException(status_code=404, detail={"code": "student_not_found", "message": "Клиент не найден"})
    discounts = list((await session.execute(select(Discount).join(DiscountAssignment, DiscountAssignment.discount_id == Discount.id).where(DiscountAssignment.student_id == student_id, DiscountAssignment.club_id == actor.club_id, Discount.club_id == actor.club_id, Discount.is_active.is_(True)))).scalars().all())
    return {"club_id": actor.club_id, "student_id": student_id, "discounts": [{"id": d.id, "name": d.name, "kind": d.kind, "value": d.value, "scope": d.scope} for d in discounts], "read_only": True}


@overview_router.get("/data")
async def overview_data(
    request: Request,
    context: AuthContext | None = Depends(web_context),
    session: AsyncSession = Depends(get_session),
    date_from: str | None = Query(default=None, max_length=10),
    date_to: str | None = Query(default=None, max_length=10),
):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра сводки"})
    students = list((await session.execute(select(Student).where(Student.club_id == actor.club_id))).scalars().all())
    visits = list((await session.execute(select(VisitLog).where(VisitLog.club_id == actor.club_id))).scalars().all())
    if date_from or date_to:
        def in_period(visit):
            value = getattr(visit, "visited_at", None)
            if not value: return False
            day = value.date().isoformat()
            return (not date_from or day >= date_from) and (not date_to or day <= date_to)
        visits = [visit for visit in visits if in_period(visit)]
    from datetime import datetime, timezone
    metrics = calculate_admin_dashboard(students, visit_logs=visits)
    club = await session.scalar(select(Club).where(Club.id == actor.club_id)) if hasattr(session, "scalar") else None
    settings = (club.club_settings or {}) if club else {}
    timeout = int((settings.get("limits", {}) or {}).get("session_timeout_minutes", 150) or 150)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    by_id = {s.id: s for s in students}; active_sessions = []; past_sessions = []
    for visit in sorted(visits, key=lambda x: getattr(x, "visited_at", None) or datetime.min, reverse=True):
        student = by_id.get(getattr(visit, "student_id", None)); visited = getattr(visit, "visited_at", None)
        if not student or not visited: continue
        visited = visited.replace(tzinfo=None); end = visited + timedelta(minutes=timeout); item = {"student_id": student.id, "name": student.name, "balance": student.balance_lessons or 0, "parent_id": student.parent_id, "last_visit": (visited + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M"), "session_end": (end + timedelta(hours=3)).strftime("%H:%M"), "time_passed_mins": max(0, int((now - visited).total_seconds() // 60))}
        if now < end: item["mins_left"] = max(0, int((end - now).total_seconds() // 60)); active_sessions.append(item)
        else: past_sessions.append(item)
    metrics.update({"active_sessions": active_sessions, "past_sessions": past_sessions, "session_timeout_minutes": timeout})
    return {"club_id": actor.club_id, "metrics": metrics, "read_only": True}


@overview_router.get("/search")
async def staff_global_search(request: Request, q: str = Query(default="", max_length=100), context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    value = str(q or "").strip()
    if not value:
        return {"club_id": actor.club_id, "results": [], "read_only": True}
    pattern = f"%{value}%"
    students = list((await session.execute(select(Student).where(Student.club_id == actor.club_id, or_(Student.name.ilike(pattern), Student.parent_phone.ilike(pattern), Student.parent_phone_secondary.ilike(pattern))).order_by(Student.name).limit(20))).scalars().all())
    return {"club_id": actor.club_id, "results": [{"type": "athlete", "id": s.id, "name": s.name, "discipline": s.discipline} for s in students], "read_only": True}


@overview_router.get("/visits")
async def staff_visits_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session), date_from: str | None = Query(default=None, max_length=10), date_to: str | None = Query(default=None, max_length=10), q: str | None = Query(default=None, max_length=100)):
    """Admin attendance: one row per athlete session, not per repeat check-in."""
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    students = list((await session.execute(select(Student).where(Student.club_id == actor.club_id).order_by(Student.name))).scalars().all())
    by_id = {student.id: student for student in students}
    search = (q or "").strip().casefold()
    selected_ids = {student.id for student in students if not search or search in str(student.name).casefold()}
    club = await session.scalar(select(Club).where(Club.id == actor.club_id))
    settings = (club.club_settings or {}) if club else {}
    timeout_minutes = int((settings.get("limits", {}) or {}).get("session_timeout_minutes", 150) or 150)
    visits = list((await session.execute(select(VisitLog).where(VisitLog.club_id == actor.club_id).order_by(VisitLog.visited_at.asc()))).scalars().all())

    def in_period(value):
        if not value:
            return False
        day = (value.replace(tzinfo=None) + timedelta(hours=3)).date().isoformat()
        return (not date_from or day >= date_from) and (not date_to or day <= date_to)

    by_student = {}
    for visit in visits:
        student_id, value = getattr(visit, "student_id", None), getattr(visit, "visited_at", None)
        if student_id in selected_ids and value:
            by_student.setdefault(student_id, []).append(value.replace(tzinfo=None))

    rows, timeout = [], timedelta(minutes=max(1, timeout_minutes))
    for student_id, timestamps in by_student.items():
        current = []
        for timestamp in timestamps + [None]:
            if timestamp is not None and (not current or timestamp - current[-1] <= timeout):
                current.append(timestamp)
                continue
            if current and in_period(current[0]):
                rows.append({"student_id": student_id, "student_name": by_id[student_id].name, "visited_at": (current[0] + timedelta(hours=3)).isoformat(sep=" ", timespec="minutes"), "last_checkin": (current[-1] + timedelta(hours=3)).isoformat(sep=" ", timespec="minutes"), "checkins_count": len(current)})
            current = [timestamp] if timestamp is not None else []
    rows.sort(key=lambda row: row["visited_at"], reverse=True)
    return {"club_id": actor.club_id, "sessions": rows, "session_count": len(rows), "timeout_minutes": timeout_minutes, "filters": {"date_from": date_from or "", "date_to": date_to or "", "q": q or ""}, "read_only": True}


@overview_router.get("/stats")
async def full_stats_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    students = list((await session.execute(select(Student).where(Student.club_id == actor.club_id))).scalars().all())
    visits = list((await session.execute(select(VisitLog).where(VisitLog.club_id == actor.club_id))).scalars().all())
    payments = list((await session.execute(select(PaymentOrder).where(PaymentOrder.club_id == actor.club_id, PaymentOrder.status == "CONFIRMED"))).scalars().all())
    orders = list((await session.execute(select(CartOrder).where(CartOrder.club_id == actor.club_id, CartOrder.status == "CONFIRMED"))).scalars().all())
    cash = list((await session.execute(select(CashEntry).where(CashEntry.club_id == actor.club_id))).scalars().all())
    metrics = calculate_student_metrics(students, visit_logs=visits); revenue = calculate_revenue_periods(payments + orders); flow = calculate_cash_flow_periods(cash, cash_sales=payments + orders)
    visit_counts = {}
    for visit in visits: visit_counts[visit.student_id] = visit_counts.get(visit.student_id, 0) + 1
    by_id = {student.id: student for student in students}
    top_athletes = [{"student_id": sid, "name": getattr(by_id.get(sid), "name", ""), "visits": count} for sid, count in sorted(visit_counts.items(), key=lambda item: item[1], reverse=True)[:20]]
    churned_students = [{"student_id": student.id, "name": student.name, "last_visit": getattr(student, "last_visit", None).isoformat() if getattr(student, "last_visit", None) else None} for student in students if student.id not in visit_counts and not getattr(student, "is_frozen", False)][:20]
    return {"club_id": actor.club_id, "metrics": {key: metrics.get(key) for key in ("total_athletes", "total_parents", "retention_rate", "active_passes", "frozen_passes", "burning_passes", "inactive_passes", "total_lessons_left", "discipline_counts")}, "revenue": revenue, "cash_flow": flow, "visits_by_student": visit_counts, "top_athletes": top_athletes, "churned_students": churned_students, "read_only": True}


@cash_router.get("/data")
async def cash_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session), entry_type: str | None = Query(default=None, max_length=20), q: str | None = Query(default=None, max_length=100)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "cash_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра кассы"})
    entries = list((await session.execute(select(CashEntry).where(CashEntry.club_id == actor.club_id))).scalars().all())
    income = sum(int(getattr(entry, "amount_kopecks", 0) or 0) for entry in entries if entry.entry_type == "income")
    expense = sum(int(getattr(entry, "amount_kopecks", 0) or 0) for entry in entries if entry.entry_type == "expense")
    rows = [{"id": getattr(entry, "id", None), "entry_type": entry.entry_type, "amount_kopecks": entry.amount_kopecks, "description": getattr(entry, "description", ""), "category": getattr(entry, "category", ""), "created_at": getattr(entry, "created_at", None).isoformat() if getattr(entry, "created_at", None) else None, "reversed_entry_id": getattr(entry, "reversed_entry_id", None)} for entry in entries]
    type_value = entry_type if isinstance(entry_type, str) else ""; q_value = q if isinstance(q, str) else ""
    if type_value.strip(): rows = [row for row in rows if row["entry_type"] == type_value.strip()]
    if q_value.strip(): rows = [row for row in rows if q_value.strip().casefold() in f'{row["description"]} {row["category"]}'.casefold()]
    return {"club_id": actor.club_id, "income_kopecks": income, "expense_kopecks": expense, "balance_kopecks": income - expense, "entries": rows, "filters": {"entry_type": type_value, "q": q_value}, "read_only": True}


@cash_router.post("/entries")
async def create_cash_entry_web(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_CASH_MUTATIONS_ENABLED", "0") != "1":
        raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type == "staff" and "cash_sale" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request)
    payload = await request.json()
    if set(payload) - {"entry_type", "category", "amount_kopecks", "description", "idempotency_key"}:
        raise HTTPException(status_code=400, detail={"code": "invalid_cash_fields"})
    entry_type = str(payload.get("entry_type") or "").strip().lower()
    category = str(payload.get("category") or "other").strip()[:50]
    description = str(payload.get("description") or "").strip()
    key = str(payload.get("idempotency_key") or "").strip()
    try:
        amount = int(payload.get("amount_kopecks"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail={"code": "invalid_amount"})
    if entry_type not in {"income", "expense"} or not 1 <= amount <= 100_000_000_00 or not key or len(key) > 100 or len(description) > 500:
        raise HTTPException(status_code=400, detail={"code": "invalid_cash_entry"})
    accepted = await request.app.state.redis_client.set(f"web:cash-entry:{actor.club_id}:{key}", "1", ex=900, nx=True)
    if not accepted:
        return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id}
    entry = CashEntry(club_id=actor.club_id, entry_type=entry_type, category=category, amount_kopecks=amount, description=description, idempotency_key=f"web:{actor.club_id}:{key}"[:80], created_by=actor.user_id)
    session.add(entry)
    await session.commit()
    audit_event("web_cash_entry_created", club_id=actor.club_id, actor_user_id=actor.user_id, action="create", object_type="cash_entry", object_id=entry.id, location="web/staff/cash", amount_kopecks=amount, method="cash")
    return {"ok": True, "club_id": actor.club_id, "entry_id": entry.id, "read_only": False}


@cash_router.post("/entries/{entry_id}/reverse")
async def reverse_cash_entry_web(entry_id: int, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_CASH_MUTATIONS_ENABLED", "0") != "1":
        raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type == "staff" and "cash_sale" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request)
    payload = await request.json()
    if set(payload) - {"reason", "idempotency_key"}:
        raise HTTPException(status_code=400, detail={"code": "invalid_reversal_fields"})
    reason = str(payload.get("reason") or "").strip()
    key = str(payload.get("idempotency_key") or "").strip()
    if not 3 <= len(reason) <= 500 or not key or len(key) > 100:
        raise HTTPException(status_code=400, detail={"code": "invalid_reversal"})
    accepted = await request.app.state.redis_client.set(f"web:cash-reversal:{actor.club_id}:{entry_id}:{key}", "1", ex=900, nx=True)
    if not accepted:
        return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id, "entry_id": entry_id}
    entry = await session.scalar(select(CashEntry).where(CashEntry.id == entry_id, CashEntry.club_id == actor.club_id).with_for_update())
    if not entry:
        raise HTTPException(status_code=404, detail={"code": "cash_entry_not_found"})
    if entry.reversed_entry_id:
        raise HTTPException(status_code=409, detail={"code": "cash_entry_already_reversed"})
    reversal = CashEntry(club_id=actor.club_id, entry_type="expense" if entry.entry_type == "income" else "income", category="reversal", amount_kopecks=entry.amount_kopecks, description=f"Reversal #{entry.id}: {reason}"[:500], idempotency_key=f"web-reversal:{actor.club_id}:{entry.id}:{key}"[:80], created_by=actor.user_id)
    session.add(reversal)
    await session.flush()
    entry.reversed_entry_id = reversal.id
    await session.commit()
    audit_event("web_cash_entry_reversed", club_id=actor.club_id, actor_user_id=actor.user_id, action="reverse", object_type="cash_entry", object_id=entry_id, location="web/staff/cash", amount_kopecks=entry.amount_kopecks, method="cash", reason=reason)
    return {"ok": True, "club_id": actor.club_id, "entry_id": entry_id, "reversal_id": reversal.id, "read_only": False}


@cash_router.delete("/entries/{entry_id}")
async def delete_cash_entry_web(entry_id: int, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "cash_sale" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    if actor.actor_type not in {"owner", "staff"}: raise HTTPException(status_code=403, detail={"code": "staff_access_required"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json(); key = str(payload.get("idempotency_key") or "").strip()
    if not key or len(key) > 100: raise HTTPException(status_code=400, detail={"code": "invalid_idempotency_key"})
    if not await request.app.state.redis_client.set(f"web:cash-delete:{actor.club_id}:{entry_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True}
    entry = await session.scalar(select(CashEntry).where(CashEntry.id == entry_id, CashEntry.club_id == actor.club_id).with_for_update())
    if not entry: raise HTTPException(status_code=404, detail={"code": "cash_entry_not_found"})
    if entry.reversed_entry_id: raise HTTPException(status_code=409, detail={"code": "cash_entry_has_reversal"})
    await session.delete(entry); await session.commit(); audit_event("web_cash_entry_deleted", club_id=actor.club_id, actor_user_id=actor.user_id, action="delete", object_type="cash_entry", object_id=entry_id, location="web/staff/cash")
    return {"ok": True, "entry_id": entry_id}

@sales_router.get("/data")
async def sales_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session), q: str | None = Query(default=None, max_length=100), source: str | None = Query(default=None, max_length=20), sale_type: str | None = Query(default=None, max_length=80)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра продаж"})
    payments = list((await session.execute(select(PaymentOrder).where(PaymentOrder.club_id == actor.club_id, PaymentOrder.status == "CONFIRMED"))).scalars().all())
    orders = list((await session.execute(select(CartOrder).where(CartOrder.club_id == actor.club_id, CartOrder.status == "CONFIRMED"))).scalars().all())
    students = list((await session.execute(select(Student).where(Student.club_id == actor.club_id))).scalars().all())
    students_by_id = {student.id: student.name for student in students}
    rows = []
    for row in payments + orders:
        rows.append({"id": str(getattr(row, "id", "")), "student_id": getattr(row, "student_id", None), "student_name": students_by_id.get(getattr(row, "student_id", None)), "user_id": getattr(row, "user_id", None), "amount_kopecks": int(getattr(row, "amount_kopecks", 0) or 0), "created_at": getattr(row, "created_at", None).isoformat() if getattr(row, "created_at", None) else None, "type": getattr(row, "type", None) or "cart", "source": "payment" if row in payments else "cart"})
    rows.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    q_value = q if isinstance(q, str) else ""; source_value = source if isinstance(source, str) else ""; type_value = sale_type if isinstance(sale_type, str) else ""
    if q_value.strip(): rows = [item for item in rows if q_value.strip().casefold() in str(item.get("id", "")).casefold() or q_value.strip().casefold() in str(item.get("student_name", "")).casefold() or q_value.strip() in str(item.get("student_id", "")) or q_value.strip() in str(item.get("user_id", ""))]
    if source_value.strip(): rows = [item for item in rows if item.get("source") == source_value.strip()]
    if type_value.strip(): rows = [item for item in rows if item.get("type") == type_value.strip()]
    return {"club_id": actor.club_id, "sales_count": len(rows), "amount_kopecks": sum(item["amount_kopecks"] for item in rows), "sales": rows[:100], "filters": {"q": q_value, "source": source_value, "sale_type": type_value}, "options": {"sources": ["payment", "cart"], "types": sorted({str(item.get("type")) for item in rows if item.get("type")})}, "pagination": {"limit": 100, "offset": 0, "returned": min(len(rows), 100)}, "read_only": True}


@sales_router.get("/{order_id}")
async def sale_detail_data(order_id: str, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    payment = await session.scalar(select(PaymentOrder).where(PaymentOrder.id == order_id, PaymentOrder.club_id == actor.club_id, PaymentOrder.status == "CONFIRMED"))
    if payment: return {"club_id": actor.club_id, "sale": {"id": payment.id, "student_id": payment.student_id, "amount_kopecks": payment.amount_kopecks, "created_at": payment.created_at.isoformat() if payment.created_at else None, "type": payment.type, "source": "payment", "items": []}, "read_only": True}
    order = await session.scalar(select(CartOrder).where(CartOrder.id == order_id, CartOrder.club_id == actor.club_id, CartOrder.status == "CONFIRMED"))
    if not order: raise HTTPException(status_code=404, detail={"code": "sale_not_found"})
    items = list((await session.execute(select(CartItem).where(CartItem.cart_order_id == order.id))).scalars().all())
    return {"club_id": actor.club_id, "sale": {"id": order.id, "user_id": order.user_id, "amount_kopecks": order.amount_kopecks, "created_at": order.created_at.isoformat() if order.created_at else None, "type": "cart", "source": "cart", "items": [{"title": item.title, "quantity": item.quantity, "unit_price_kopecks": item.unit_price_kopecks} for item in items]}, "read_only": True}


@audit_router.get("/data")
async def audit_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session), limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0), event: str | None = Query(default=None, max_length=80), actor_role: str | None = Query(default=None, max_length=32)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра аудита"})
    query = select(AuditEntry).where(AuditEntry.club_id == actor.club_id)
    if event: query = query.where(AuditEntry.event == event.strip())
    if actor_role: query = query.where(AuditEntry.actor_role == actor_role.strip().casefold())
    entries = list((await session.execute(query.order_by(AuditEntry.created_at.desc()).offset(offset).limit(limit))).scalars().all())
    return {"club_id": actor.club_id, "entries": [{"id": entry.id, "event": entry.event, "action": entry.action, "object_type": entry.object_type, "created_at": entry.created_at.isoformat() if entry.created_at else None} for entry in entries], "filters": {"event": event or "", "actor_role": actor_role or ""}, "pagination": {"limit": limit, "offset": offset, "returned": len(entries)}, "read_only": True}


@audit_router.get("/{entry_id}")
async def audit_detail_data(entry_id: int, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions: raise HTTPException(status_code=403, detail={"code":"permission_denied"})
    entry = await session.scalar(select(AuditEntry).where(AuditEntry.id == entry_id, AuditEntry.club_id == actor.club_id))
    if not entry: raise HTTPException(status_code=404, detail={"code":"audit_not_found"})
    payload = entry.payload if isinstance(entry.payload, dict) else {}
    safe_payload = {str(k): str(v)[:200] for k,v in payload.items() if k not in {"token","secret","init_data","authorization","cookie"}}
    return {"club_id": actor.club_id, "entry": {"id": entry.id, "event": entry.event, "action": entry.action, "object_type": entry.object_type, "created_at": entry.created_at.isoformat() if entry.created_at else None, "payload": safe_payload}, "read_only": True}


@schedule_router.get("/data")
async def schedule_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "schedule_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра расписания"})
    club = await session.scalar(select(Club).where(Club.id == actor.club_id))
    if not club:
        raise HTTPException(status_code=404, detail={"code": "club_not_found", "message": "Клуб не найден"})
    settings = club.club_settings if isinstance(club.club_settings, dict) else {}
    disciplines = settings.get("disciplines", {}) if isinstance(settings.get("disciplines", {}), dict) else {}
    schedule = {name: (block.get("schedule", {}) if isinstance(block, dict) else {}) for name, block in disciplines.items()}
    return {"club_id": actor.club_id, "schedule": schedule, "read_only": True}


@schedule_router.patch("")
async def update_schedule(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_SCHEDULE_MUTATIONS_ENABLED", "0") != "1":
        raise HTTPException(status_code=404, detail={"code": "feature_disabled", "message": "Web изменение расписания пока отключено"})
    if actor.actor_type == "staff" and "schedule_edit" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права изменения расписания"})
    await require_csrf(request.app.state.redis_client, request)
    payload = await request.json()
    key = str(payload.get("idempotency_key") or "").strip()
    if not key or len(key) > 100:
        raise HTTPException(status_code=400, detail={"code": "invalid_idempotency_key", "message": "Требуется idempotency key"})
    accepted = await request.app.state.redis_client.set(f"web:schedule:{actor.club_id}:{key}", "1", ex=300, nx=True)
    if not accepted:
        return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id}
    discipline = str(payload.get("discipline") or "").strip()
    day = str(payload.get("day") or "").strip().lower()
    if day not in {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}:
        raise HTTPException(status_code=400, detail={"code": "invalid_day", "message": "Недопустимый день"})
    club = await session.scalar(select(Club).where(Club.id == actor.club_id).with_for_update())
    settings = dict(club.club_settings or {}) if club else {}
    disciplines = settings.get("disciplines", {})
    if not club or discipline not in disciplines or not isinstance(disciplines[discipline], dict):
        raise HTTPException(status_code=404, detail={"code": "discipline_not_found", "message": "Дисциплина не найдена"})
    block = dict(disciplines[discipline])
    schedule = normalize_schedule_block(block.get("schedule", {}))
    lessons = payload.get("lessons", [])
    if not isinstance(lessons, list) or len(lessons) > 100:
        raise HTTPException(status_code=400, detail={"code": "invalid_lessons", "message": "Некорректное расписание"})
    for lesson in lessons:
        if not isinstance(lesson, dict) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(lesson.get("time", ""))):
            raise HTTPException(status_code=400, detail={"code": "invalid_lesson_time", "message": "Время занятия должно быть в формате HH:MM"})
        duration = lesson.get("duration", 60)
        if not isinstance(duration, int) or duration < 15 or duration > 240:
            raise HTTPException(status_code=400, detail={"code": "invalid_lesson_duration", "message": "Недопустимая длительность занятия"})
        allowed_fields = {"time", "duration", "coach", "group", "capacity", "discipline"}
        if set(lesson) - allowed_fields:
            raise HTTPException(status_code=400, detail={"code": "invalid_lesson_fields", "message": "Недопустимые поля занятия"})
        for field in ("coach", "group", "discipline"):
            if field in lesson and (not isinstance(lesson[field], str) or len(lesson[field]) > 120):
                raise HTTPException(status_code=400, detail={"code": "invalid_lesson_field", "message": "Некорректное поле занятия"})
        if "capacity" in lesson and (not isinstance(lesson["capacity"], int) or lesson["capacity"] < 1 or lesson["capacity"] > 1000):
            raise HTTPException(status_code=400, detail={"code": "invalid_lesson_capacity", "message": "Некорректная вместимость занятия"})
    schedule[day] = lessons
    block["schedule"] = schedule
    disciplines[discipline] = block
    settings["disciplines"] = disciplines
    club.club_settings = settings
    await session.commit()
    audit_event("web_schedule_updated", club_id=actor.club_id, actor_user_id=actor.user_id, action="update", object_type="schedule", object_id=discipline, location="web/staff/schedule", day=day, lesson_count=len(lessons))
    return {"ok": True, "club_id": actor.club_id, "discipline": discipline, "day": day, "lesson_count": len(lessons)}


@catalog_router.get("/products")
async def products_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "products_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра товаров"})
    products = list((await session.execute(select(ClubProduct).where(ClubProduct.club_id == actor.club_id).order_by(ClubProduct.name))).scalars().all())
    return {"club_id": actor.club_id, "products": [{"id": p.id, "name": p.name, "category": p.category, "price_kopecks": p.price_kopecks, "stock": p.stock, "is_active": bool(p.is_active) if hasattr(p, "is_active") else True, "image_url": p.image_url if hasattr(p, "image_url") else None, "details": p.details if hasattr(p, "details") else None} for p in products], "read_only": True}


@catalog_router.post("/products")
async def create_product_web(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_CATALOG_MUTATIONS_ENABLED", "0") != "1": raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type == "staff" and "products_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json()
    if set(payload) - {"name", "category", "price_kopecks", "stock", "details", "image_url", "idempotency_key"}: raise HTTPException(status_code=400, detail={"code": "invalid_product_fields"})
    key = str(payload.get("idempotency_key") or "").strip(); name = str(payload.get("name") or "").strip(); category = str(payload.get("category") or "other").strip(); details = str(payload.get("details") or "").strip()
    try: price = int(payload.get("price_kopecks")); stock = int(payload.get("stock", 0))
    except (TypeError, ValueError): raise HTTPException(status_code=400, detail={"code": "invalid_product_numbers"})
    if not key or len(key) > 100 or not 2 <= len(name) <= 120 or len(category) > 30 or len(details) > 1000 or price < 0 or stock < 0: raise HTTPException(status_code=400, detail={"code": "invalid_product"})
    if not await request.app.state.redis_client.set(f"web:product-create:{actor.club_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id}
    image_url = str(payload.get("image_url") or "").strip()
    if len(image_url) > 500 or (image_url and not image_url.startswith("/static/uploads/products/")): raise HTTPException(status_code=400, detail={"code": "invalid_product_image"})
    product = ClubProduct(club_id=actor.club_id, name=name, category=category, price_kopecks=price, stock=stock, details=details, image_url=image_url or None, is_active=True); session.add(product); await session.commit()
    audit_event("web_product_created", club_id=actor.club_id, actor_user_id=actor.user_id, action="create", object_type="club_product", object_id=product.id, location="web/staff/products")
    return {"ok": True, "club_id": actor.club_id, "product_id": product.id, "read_only": False}


@catalog_router.post("/products/upload-image")
async def upload_product_image_web(request: Request, file: UploadFile = File(...), context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if os.getenv("WEB_CATALOG_MUTATIONS_ENABLED", "0") != "1": raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type == "staff" and "products_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request)
    if file.content_type not in {"image/jpeg", "image/png", "image/webp"}: raise HTTPException(status_code=400, detail={"code": "invalid_image_type"})
    data = await file.read()
    if not data or len(data) > 5 * 1024 * 1024: raise HTTPException(status_code=400, detail={"code": "invalid_image_size"})
    signatures = {"image/jpeg": (b"\xff\xd8\xff",), "image/png": (b"\x89PNG\r\n\x1a\n",), "image/webp": (b"RIFF",)}
    if not any(data.startswith(signature) for signature in signatures[file.content_type]): raise HTTPException(status_code=400, detail={"code": "invalid_image_content"})
    extension = {"image/jpeg":"jpg", "image/png":"png", "image/webp":"webp"}[file.content_type]
    filename = f"web_{uuid.uuid4().hex}.{extension}"; directory = Path("static/uploads/products"); directory.mkdir(parents=True, exist_ok=True); (directory / filename).write_bytes(data)
    audit_event("web_product_image_uploaded", club_id=actor.club_id, actor_user_id=actor.user_id, action="upload", object_type="product_image", location="web/staff/products")
    return {"ok": True, "image_url": f"/static/uploads/products/{filename}"}


@catalog_router.patch("/products/{product_id}")
async def update_product_web(product_id: int, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_CATALOG_MUTATIONS_ENABLED", "0") != "1": raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type == "staff" and "products_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json()
    if set(payload) - {"name", "category", "price_kopecks", "details", "is_active", "idempotency_key"}: raise HTTPException(status_code=400, detail={"code": "invalid_product_fields"})
    key = str(payload.get("idempotency_key") or "").strip()
    if not key or len(key) > 100: raise HTTPException(status_code=400, detail={"code": "invalid_idempotency_key"})
    if not await request.app.state.redis_client.set(f"web:product-update:{actor.club_id}:{product_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id, "product_id": product_id}
    product = await session.scalar(select(ClubProduct).where(ClubProduct.id == product_id, ClubProduct.club_id == actor.club_id).with_for_update())
    if not product: raise HTTPException(status_code=404, detail={"code": "product_not_found"})
    if "name" in payload:
        name = str(payload["name"] or "").strip()
        if not 2 <= len(name) <= 120: raise HTTPException(status_code=400, detail={"code": "invalid_product_name"})
        product.name = name
    if "category" in payload: product.category = str(payload["category"] or "other").strip()[:30]
    if "details" in payload: product.details = str(payload["details"] or "").strip()[:1000]
    if "is_active" in payload:
        if not isinstance(payload["is_active"], bool): raise HTTPException(status_code=400, detail={"code": "invalid_product_status"})
        product.is_active = payload["is_active"]
    if "price_kopecks" in payload:
        try: product.price_kopecks = int(payload["price_kopecks"])
        except (TypeError, ValueError): raise HTTPException(status_code=400, detail={"code": "invalid_product_price"})
        if product.price_kopecks < 0: raise HTTPException(status_code=400, detail={"code": "invalid_product_price"})
    await session.commit(); audit_event("web_product_updated", club_id=actor.club_id, actor_user_id=actor.user_id, action="update", object_type="club_product", object_id=product_id, location="web/staff/products")
    return {"ok": True, "club_id": actor.club_id, "product_id": product_id, "read_only": False}


@catalog_router.delete("/products/{product_id}")
async def archive_product_web(product_id: int, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_CATALOG_MUTATIONS_ENABLED", "0") != "1": raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type == "staff" and "products_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json()
    reason = str(payload.get("reason") or "").strip(); key = str(payload.get("idempotency_key") or "").strip()
    if set(payload) - {"reason", "idempotency_key"} or not 3 <= len(reason) <= 500 or not key or len(key) > 100: raise HTTPException(status_code=400, detail={"code": "invalid_archive_request"})
    if not await request.app.state.redis_client.set(f"web:product-archive:{actor.club_id}:{product_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id, "product_id": product_id}
    product = await session.scalar(select(ClubProduct).where(ClubProduct.id == product_id, ClubProduct.club_id == actor.club_id).with_for_update())
    if not product: raise HTTPException(status_code=404, detail={"code": "product_not_found"})
    product.is_active = False; await session.commit(); audit_event("web_product_archived", club_id=actor.club_id, actor_user_id=actor.user_id, action="archive", object_type="club_product", object_id=product_id, location="web/staff/products", reason=reason)
    return {"ok": True, "club_id": actor.club_id, "product_id": product_id, "archived": True, "read_only": False}


@catalog_router.get("/products/{product_id}")
async def product_detail_data(product_id: int, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "products_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра товара"})
    product = await session.scalar(select(ClubProduct).where(ClubProduct.id == product_id, ClubProduct.club_id == actor.club_id, ClubProduct.is_active.is_(True)))
    if not product:
        raise HTTPException(status_code=404, detail={"code": "product_not_found", "message": "Товар не найден"})
    return {"club_id": actor.club_id, "product": {"id": product.id, "name": product.name, "category": product.category, "price_kopecks": product.price_kopecks, "stock": product.stock, "details": product.details}, "read_only": True}


@catalog_router.get("/products/{product_id}/history")
async def product_history_data(product_id: int, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "products_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    product = await session.scalar(select(ClubProduct).where(ClubProduct.id == product_id, ClubProduct.club_id == actor.club_id))
    if not product:
        raise HTTPException(status_code=404, detail={"code": "product_not_found"})
    # AuditEntry.object_id is stored as text; PostgreSQL does not implicitly
    # compare varchar to the integer path parameter.
    entries = list((await session.execute(select(AuditEntry).where(AuditEntry.club_id == actor.club_id, AuditEntry.object_type == "club_product", AuditEntry.object_id == str(product_id)).order_by(AuditEntry.created_at.desc()).limit(100))).scalars().all())
    return {"club_id": actor.club_id, "product_id": product_id, "history": [{"id": entry.id, "event": entry.event, "action": entry.action, "created_at": entry.created_at.isoformat() if entry.created_at else None, "payload": {str(k): str(v)[:200] for k, v in (entry.payload or {}).items() if k not in {"token", "secret", "cookie"}}} for entry in entries], "read_only": True}


@catalog_router.post("/products/{product_id}/stock")
async def adjust_product_stock_web(product_id: int, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_INVENTORY_MUTATIONS_ENABLED", "0") != "1":
        raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type == "staff" and "products_manage" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request)
    payload = await request.json()
    if set(payload) - {"delta", "reason", "idempotency_key"}:
        raise HTTPException(status_code=400, detail={"code": "invalid_stock_fields"})
    try:
        delta = int(payload.get("delta"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail={"code": "invalid_stock_delta"})
    reason = str(payload.get("reason") or "").strip()
    key = str(payload.get("idempotency_key") or "").strip()
    if delta == 0 or abs(delta) > 100_000 or not 3 <= len(reason) <= 500 or not key or len(key) > 100:
        raise HTTPException(status_code=400, detail={"code": "invalid_stock_adjustment"})
    accepted = await request.app.state.redis_client.set(f"web:stock:{actor.club_id}:{product_id}:{key}", "1", ex=900, nx=True)
    if not accepted:
        return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id, "product_id": product_id}
    product = await session.scalar(select(ClubProduct).where(ClubProduct.id == product_id, ClubProduct.club_id == actor.club_id).with_for_update())
    if not product:
        raise HTTPException(status_code=404, detail={"code": "product_not_found"})
    new_stock = int(product.stock or 0) + delta
    if new_stock < 0:
        raise HTTPException(status_code=409, detail={"code": "insufficient_stock"})
    product.stock = new_stock
    await session.commit()
    audit_event("web_product_stock_adjusted", club_id=actor.club_id, actor_user_id=actor.user_id, action="update", object_type="club_product", object_id=product_id, location="web/staff/products", quantity=delta, reason=reason)
    return {"ok": True, "club_id": actor.club_id, "product_id": product_id, "stock": new_stock, "read_only": False}


@sales_router.post("/refunds/{order_id}/decision")
async def staff_refund_decision(order_id: str, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "payments_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    if actor.actor_type not in {"owner", "staff"}: raise HTTPException(status_code=403, detail={"code": "staff_access_required"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json(); decision = str(payload.get("decision") or "").strip().lower(); reason = str(payload.get("reason") or "").strip(); key = str(payload.get("idempotency_key") or "").strip()
    if decision not in {"approve", "reject"} or not 3 <= len(reason) <= 500 or not key or len(key) > 100: raise HTTPException(status_code=400, detail={"code": "invalid_refund_decision"})
    if not await request.app.state.redis_client.set(f"web:refund-decision:{actor.club_id}:{order_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True, "order_id": order_id}
    order = await session.scalar(select(CartOrder).where(CartOrder.id == order_id, CartOrder.club_id == actor.club_id).with_for_update()) or await session.scalar(select(PaymentOrder).where(PaymentOrder.id == order_id, PaymentOrder.club_id == actor.club_id).with_for_update())
    if not order or order.status != "REFUND_REQUESTED": raise HTTPException(status_code=409, detail={"code": "refund_not_requested"})
    order.status = "REFUNDED" if decision == "approve" else "CONFIRMED"; await session.commit(); audit_event("web_refund_decision", club_id=actor.club_id, actor_user_id=actor.user_id, action=decision, object_type="order", object_id=order_id, location="web/staff/sales/refunds", reason=reason)
    return {"ok": True, "club_id": actor.club_id, "order_id": order_id, "status": order.status, "decision": decision, "provider_refund_pending": decision == "approve", "read_only": False}

@sales_router.post("/cash-product")
async def create_cash_product_sale_web(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_PRODUCT_SALES_ENABLED", "0") != "1":
        raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type == "staff" and "cash_sale" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request)
    payload = await request.json()
    if set(payload) - {"items", "buyer_user_id", "idempotency_key"} or not isinstance(payload.get("items"), list) or not 1 <= len(payload["items"]) <= 50:
        raise HTTPException(status_code=400, detail={"code": "invalid_sale_fields"})
    key = str(payload.get("idempotency_key") or "").strip()
    if not key or len(key) > 100:
        raise HTTPException(status_code=400, detail={"code": "invalid_idempotency_key"})
    accepted = await request.app.state.redis_client.set(f"web:product-sale:{actor.club_id}:{key}", "1", ex=900, nx=True)
    if not accepted:
        return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id}
    buyer_id = int(payload.get("buyer_user_id") or actor.user_id)
    buyer = await session.scalar(select(User).where(User.user_id == buyer_id, User.club_id == actor.club_id))
    if not buyer:
        raise HTTPException(status_code=404, detail={"code": "buyer_not_found"})
    product_ids = []
    for item in payload["items"]:
        if not isinstance(item, dict) or set(item) - {"product_id", "quantity", "discount_ids"}:
            raise HTTPException(status_code=400, detail={"code": "invalid_sale_item"})
        try: product_ids.append(int(item["product_id"]))
        except (TypeError, ValueError): raise HTTPException(status_code=400, detail={"code": "invalid_product_id"})
    products = list((await session.execute(select(ClubProduct).where(ClubProduct.id.in_(product_ids), ClubProduct.club_id == actor.club_id, ClubProduct.is_active.is_(True)).with_for_update())).scalars().all())
    by_id = {p.id: p for p in products}; normalized = []; original_total = total = 0; applied = []
    for item in payload["items"]:
        product = by_id.get(int(item["product_id"]))
        qty = int(item.get("quantity", 1))
        if not product or not 1 <= qty <= 99 or product.stock < qty:
            raise HTTPException(status_code=409, detail={"code": "insufficient_stock"})
        discount_ids = [int(x) for x in (item.get("discount_ids") or [])]
        discounts = await active_discounts(session, actor.club_id, buyer_id, "products", ids=discount_ids or None)
        line_original = product.price_kopecks * qty; line_total, line_applied = apply_discounts(line_original, discounts)
        original_total += line_original; total += line_total; applied.extend(line_applied); normalized.append((product, qty))
    order_id = f"WEB_CASH_{uuid.uuid4().hex[:16].upper()}"
    primary = applied[0][0] if applied else None
    order = CartOrder(id=order_id, club_id=actor.club_id, user_id=buyer_id, amount_kopecks=total, original_amount_kopecks=original_total, discount_id=primary.id if primary else None, discount_name=primary.name if primary else None, discount_amount_kopecks=original_total - total or None, status="CONFIRMED", provider_payment_id=f"CASH:{order_id}")
    session.add(order); await session.flush()
    for product, qty in normalized:
        product.stock -= qty
        session.add(CartItem(cart_order_id=order_id, product_id=product.id, item_type="product", title=product.name, quantity=qty, unit_price_kopecks=product.price_kopecks, payload={"category": product.category}))
    await session.commit()
    audit_event("web_cash_product_sale", club_id=actor.club_id, actor_user_id=actor.user_id, action="sale", object_type="cart_order", object_id=order_id, location="web/staff/sales", amount_kopecks=total, method="cash", buyer_user_id=buyer_id)
    return {"ok": True, "club_id": actor.club_id, "order_id": order_id, "amount_kopecks": total, "read_only": False}


@sales_router.post("/cash-subscription")
async def create_cash_subscription_sale_web(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_SUBSCRIPTION_SALES_ENABLED", "0") != "1":
        raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type == "staff" and "cash_sale" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request)
    payload = await request.json()
    if set(payload) - {"student_id", "discipline", "tariff_idx", "discount_ids", "idempotency_key"}:
        raise HTTPException(status_code=400, detail={"code": "invalid_subscription_sale_fields"})
    key = str(payload.get("idempotency_key") or "").strip()
    if not key or len(key) > 100:
        raise HTTPException(status_code=400, detail={"code": "invalid_idempotency_key"})
    accepted = await request.app.state.redis_client.set(f"web:subscription-sale:{actor.club_id}:{key}", "1", ex=900, nx=True)
    if not accepted:
        return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id}
    try: student_id = int(payload.get("student_id")); tariff_idx = int(payload.get("tariff_idx"))
    except (TypeError, ValueError): raise HTTPException(status_code=400, detail={"code": "invalid_subscription_sale"})
    student = await session.scalar(select(Student).where(Student.id == student_id, Student.club_id == actor.club_id).with_for_update())
    if not student:
        raise HTTPException(status_code=404, detail={"code": "student_not_found"})
    settings = dict((await session.scalar(select(Club).where(Club.id == actor.club_id))).club_settings or {})
    discipline = str(payload.get("discipline") or student.discipline or "").strip()
    cfg = (settings.get("disciplines", {}) or {}).get(discipline) or {}; tariffs = cfg.get("tariffs", [])
    if tariff_idx < 0 or tariff_idx >= len(tariffs):
        raise HTTPException(status_code=400, detail={"code": "tariff_not_found"})
    tariff = tariffs[tariff_idx] if isinstance(tariffs[tariff_idx], dict) else {}
    count = int(tariff.get("count", 0) or 0); days = int(tariff.get("days", 30) or 30); original = int(tariff.get("price", 0) or 0) * 100
    if count < 1 or days < 1 or original <= 0:
        raise HTTPException(status_code=400, detail={"code": "invalid_tariff"})
    discount_ids = [int(x) for x in (payload.get("discount_ids") or [])]
    discounts = await active_discounts(session, actor.club_id, student.parent_id, "subscriptions", student.id, discount_ids or None)
    total, applied = apply_discounts(original, discounts); primary = applied[0][0] if applied else None
    order_id = f"CASH_SUB_{uuid.uuid4().hex[:16].upper()}"
    result = await add_abon(student.id, count, session, actor.club_id, settings, days_to_add=days, discipline=discipline)
    if not result:
        raise HTTPException(status_code=409, detail={"code": "subscription_activation_failed"})
    session.add(PaymentOrder(id=order_id, user_id=student.parent_id or actor.user_id, student_id=student.id, club_id=actor.club_id, discipline=discipline, amount_kopecks=total, original_amount_kopecks=original, discount_id=primary.id if primary else None, discount_name=primary.name if primary else None, discount_amount_kopecks=original - total or None, status="CONFIRMED", type="CASH_SUBSCRIPTION", provider_payment_id=f"CASH:{order_id}", lesson_count=count, days_to_add=days))
    await session.commit()
    audit_event("web_cash_subscription_sale", club_id=actor.club_id, actor_user_id=actor.user_id, action="sale", object_type="payment_order", object_id=order_id, location="web/staff/sales", amount_kopecks=total, method="cash", student_id=student.id, lesson_count=count, days_to_add=days)
    return {"ok": True, "club_id": actor.club_id, "order_id": order_id, "student_id": student.id, "amount_kopecks": total, "read_only": False}


@client_router.get("/payment-methods")
async def client_payment_methods(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    rows = (await session.scalars(select(Subscription).where(Subscription.user_id == actor.user_id, Subscription.club_id == actor.club_id, Subscription.rebill_id.is_not(None), Subscription.is_active.is_(True)))).all()
    return {"club_id": actor.club_id, "payment_methods": [{"id": s.id, "type": "bank_card", "masked": "••••"} for s in rows], "read_only": True}

@client_router.delete("/payment-methods/{subscription_id}")
async def delete_client_payment_method(subscription_id: int, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context); await require_csrf(request.app.state.redis_client, request)
    row = await session.scalar(select(Subscription).where(Subscription.id == subscription_id, Subscription.user_id == actor.user_id, Subscription.club_id == actor.club_id, Subscription.rebill_id.is_not(None)).with_for_update())
    if not row: raise HTTPException(status_code=404, detail={"code": "payment_method_not_found"})
    row.rebill_id = None
    await session.commit()
    audit_event("web_payment_method_deleted", club_id=actor.club_id, actor_user_id=actor.user_id, action="delete", object_type="subscription_card", object_id=subscription_id, location="web/client/payment-methods")
    return {"ok": True, "club_id": actor.club_id, "subscription_id": subscription_id}

@router.post("/receipts/{order_id}/deliver")
async def deliver_web_receipt(order_id: str, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context); await require_csrf(request.app.state.redis_client, request)
    order = await session.scalar(select(PaymentOrder).where(PaymentOrder.id == order_id, PaymentOrder.club_id == actor.club_id, PaymentOrder.user_id == actor.user_id, PaymentOrder.status == "CONFIRMED"))
    if not order: raise HTTPException(status_code=404, detail={"code": "receipt_not_found"})
    club = await session.scalar(select(Club).where(Club.id == actor.club_id))
    if not club or not club.bot_token or club.bot_token not in bots_dict: raise HTTPException(status_code=503, detail={"code": "club_bot_unavailable"})
    key = str((await request.json()).get("idempotency_key") or "").strip()
    if not key or len(key) > 100: raise HTTPException(status_code=400, detail={"code": "invalid_idempotency_key"})
    if not await request.app.state.redis_client.set(f"web:receipt:{actor.club_id}:{order_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True}
    student = await session.get(Student, order.student_id) if order.student_id else None
    text = f"Оплата подтверждена\nЗаказ: {order.id}\nСумма: {order.amount_kopecks / 100:.2f} ₽\nУченик: {student.name if student else '—'}"
    await bots_dict[club.bot_token].send_message(chat_id=actor.user_id, text=text)
    audit_event("web_receipt_delivered", club_id=actor.club_id, actor_user_id=actor.user_id, action="deliver", object_type="payment_order", object_id=order.id, location="web/client/receipts")
    return {"ok": True, "order_id": order.id}

@router.post("/invitations/{student_id}")
async def create_web_invitation(student_id: int, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "student_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    if actor.actor_type not in {"owner", "staff"}: raise HTTPException(status_code=403, detail={"code": "staff_access_required"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json(); slot = int(payload.get("parent_slot") or 1)
    if slot not in {1, 2}: raise HTTPException(status_code=400, detail={"code": "invalid_parent_slot"})
    student = await session.scalar(select(Student).where(Student.id == student_id, Student.club_id == actor.club_id))
    club = await session.scalar(select(Club).where(Club.id == actor.club_id))
    phone = (student.parent_phone if slot == 1 else student.parent_phone_secondary) if student else None
    if not student: raise HTTPException(status_code=404, detail={"code": "student_not_found"})
    if not phone: raise HTTPException(status_code=409, detail={"code": "parent_phone_required"})
    linked = await session.scalar(select(StudentParent).where(StudentParent.student_id == student.id, StudentParent.phone == phone))
    if linked: return {"ok": True, "status": "linked", "student_id": student.id}
    if not club or not club.bot_token or club.bot_token not in bots_dict: raise HTTPException(status_code=503, detail={"code": "club_bot_unavailable"})
    bot_info = await bots_dict[club.bot_token].get_me(); username = str(bot_info.username or "").lstrip("@")
    if not username: raise HTTPException(status_code=503, detail={"code": "bot_username_unavailable"})
    key = str(payload.get("idempotency_key") or "").strip()
    if not key or len(key) > 100: raise HTTPException(status_code=400, detail={"code": "invalid_idempotency_key"})
    if not await request.app.state.redis_client.set(f"web:invite:{actor.club_id}:{student.id}:{slot}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True}
    url = f"https://t.me/{username}?start=invite_{student.id}_{slot}"
    audit_event("web_student_invite_created", club_id=actor.club_id, actor_user_id=actor.user_id, action="invite", object_type="student", object_id=student.id, location="web/staff/invitations", parent_slot=slot, parent_phone_tail=str(phone)[-4:])
    return {"ok": True, "status": "invited", "student_id": student.id, "url": url}

@client_router.post("/freeze/purchase")
async def purchase_freeze_web(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_FREEZE_MUTATIONS_ENABLED", "0") != "1":
        raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type == "staff" and "cash_sale" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request)
    payload = await request.json()
    if set(payload) - {"student_id", "days", "discount_ids", "idempotency_key"}:
        raise HTTPException(status_code=400, detail={"code": "invalid_freeze_fields"})
    key = str(payload.get("idempotency_key") or "").strip()
    try: student_id = int(payload.get("student_id")); days = int(payload.get("days"))
    except (TypeError, ValueError): raise HTTPException(status_code=400, detail={"code": "invalid_freeze_request"})
    if not key or len(key) > 100 or not 1 <= days <= 365:
        raise HTTPException(status_code=400, detail={"code": "invalid_freeze_request"})
    accepted = await request.app.state.redis_client.set(f"web:freeze:{actor.club_id}:{student_id}:{key}", "1", ex=900, nx=True)
    if not accepted:
        return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id, "student_id": student_id}
    student = await session.scalar(select(Student).where(Student.id == student_id, Student.club_id == actor.club_id).with_for_update())
    if not student:
        raise HTTPException(status_code=404, detail={"code": "student_not_found"})
    if actor.actor_type == "client" and student.parent_id != actor.user_id:
        linked = await session.scalar(select(StudentParent).where(StudentParent.student_id == student.id, StudentParent.parent_id == actor.user_id))
        if not linked:
            raise HTTPException(status_code=403, detail={"code": "student_scope_denied"})
    settings = dict((await session.scalar(select(Club).where(Club.id == actor.club_id))).club_settings or {})
    original = int(float(settings.get("limits", {}).get("freeze_price_per_day", 0) or 0) * days * 100)
    if original <= 0:
        raise HTTPException(status_code=409, detail={"code": "freeze_not_configured"})
    discounts = await active_discounts(session, actor.club_id, student.parent_id, "freeze", student.id, [int(x) for x in (payload.get("discount_ids") or [])] or None)
    total, applied = apply_discounts(original, discounts); primary = applied[0][0] if applied else None
    result = await purchase_student_freeze(student.id, actor.club_id, days, session)
    if not result:
        raise HTTPException(status_code=409, detail={"code": "freeze_rejected"})
    order_id = f"CASH_FREEZE_{uuid.uuid4().hex[:16].upper()}"
    session.add(PaymentOrder(id=order_id, user_id=student.parent_id or actor.user_id, student_id=student.id, club_id=actor.club_id, discipline=student.discipline, amount_kopecks=total, original_amount_kopecks=original, discount_id=primary.id if primary else None, discount_name=primary.name if primary else None, discount_amount_kopecks=original - total or None, status="CONFIRMED", type="CASH_FREEZE", provider_payment_id=f"CASH:{order_id}", lesson_count=0, days_to_add=days))
    await session.commit()
    audit_event("web_cash_freeze_sale", club_id=actor.club_id, actor_user_id=actor.user_id, action="sale", object_type="payment_order", object_id=order_id, location="web/client/freeze", amount_kopecks=total, method="cash", student_id=student.id, days=days)
    return {"ok": True, "club_id": actor.club_id, "order_id": order_id, "student_id": student.id, "amount_kopecks": total, "read_only": False}


@client_router.post("/payments/{order_id}/intent")
async def create_payment_intent_web(order_id: str, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_ONLINE_PAYMENTS_ENABLED", "0") != "1": raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    await require_csrf(request.app.state.redis_client, request)
    payload = await request.json()
    if set(payload) - {"idempotency_key"}:
        raise HTTPException(status_code=400, detail={"code": "invalid_payment_intent_fields"})
    key = str(payload.get("idempotency_key") or "").strip()
    if not key or len(key) > 100:
        raise HTTPException(status_code=400, detail={"code": "invalid_idempotency_key"})
    if not await request.app.state.redis_client.set(f"web:payment-intent:{actor.club_id}:{actor.user_id}:{order_id}:{key}", "1", ex=900, nx=True):
        return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id, "order_id": order_id}
    order = await session.scalar(select(CartOrder).where(CartOrder.id == order_id, CartOrder.club_id == actor.club_id, CartOrder.user_id == actor.user_id).with_for_update())
    if not order: raise HTTPException(status_code=404, detail={"code": "order_not_found"})
    if order.status != "NEW": raise HTTPException(status_code=409, detail={"code": "order_not_payable"})
    club = await session.scalar(select(Club).where(Club.id == actor.club_id)); settings = dict(club.club_settings or {}) if club else {}; payments = dict(settings.get("payments", {}) or {})
    shop_id = str(payments.get("yookassa_shop_id") or ""); secret = str(payments.get("yookassa_secret_key") or "")
    if not shop_id or not secret: raise HTTPException(status_code=503, detail={"code": "payment_provider_unavailable"})
    try:
        user = await session.scalar(select(User).where(User.user_id == actor.user_id, User.club_id == actor.club_id))
    except AttributeError:
        user = None
    result = await YooKassaClient(shop_id, secret, proxy_url=PROXY_URL).init_payment(order.id, int(order.amount_kopecks), actor.user_id, str(settings.get("bot_username") or "speedycrm_bot"), user_email=user.email if user else None)
    if not result.get("Success"): raise HTTPException(status_code=502, detail={"code": "payment_provider_error"})
    order.provider_payment_id = str(result.get("PaymentId")); await session.commit()
    audit_event("web_payment_intent_created", club_id=actor.club_id, actor_user_id=actor.user_id, action="create", object_type="cart_order", object_id=order.id, location="web/client/payments", amount_kopecks=order.amount_kopecks, method="online")
    return {"ok": True, "club_id": actor.club_id, "order_id": order.id, "payment_id": order.provider_payment_id, "payment_url": result.get("PaymentURL"), "read_only": False}


@catalog_router.get("/discounts")
async def discounts_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра скидок"})
    discounts = list((await session.execute(select(Discount).where(Discount.club_id == actor.club_id, Discount.is_active.is_(True)).order_by(Discount.priority, Discount.name))).scalars().all())
    return {"club_id": actor.club_id, "discounts": [{"id": d.id, "name": d.name, "kind": d.kind, "value": d.value, "scope": d.scope} for d in discounts], "read_only": True}


@catalog_router.post("/discounts")
async def create_discount_web(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_PRICING_MUTATIONS_ENABLED", "0") != "1": raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type == "staff" and "tariffs_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json()
    allowed = {"name", "kind", "value", "scope", "priority", "comment", "idempotency_key"}
    if set(payload) - allowed: raise HTTPException(status_code=400, detail={"code": "invalid_discount_fields"})
    key = str(payload.get("idempotency_key") or "").strip(); name = str(payload.get("name") or "").strip(); kind = str(payload.get("kind") or "").strip().lower(); scope = str(payload.get("scope") or "all").strip().lower()
    try: value = int(payload.get("value")); priority = int(payload.get("priority", 100))
    except (TypeError, ValueError): raise HTTPException(status_code=400, detail={"code": "invalid_discount_numbers"})
    if not key or len(key) > 100 or not 2 <= len(name) <= 120 or kind not in {"percent", "fixed"} or scope not in {"all", "products", "subscriptions", "freeze"} or priority < 0 or (kind == "percent" and not 1 <= value <= 100) or (kind == "fixed" and not 1 <= value <= 100_000_000): raise HTTPException(status_code=400, detail={"code": "invalid_discount"})
    if not await request.app.state.redis_client.set(f"web:discount-create:{actor.club_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id}
    discount = Discount(club_id=actor.club_id, name=name, kind=kind, value=value, scope=scope, priority=priority, comment=str(payload.get("comment") or "")[:500], is_active=True); session.add(discount); await session.commit()
    audit_event("web_discount_created", club_id=actor.club_id, actor_user_id=actor.user_id, action="create", object_type="discount", object_id=discount.id, location="web/staff/discounts")
    return {"ok": True, "club_id": actor.club_id, "discount_id": discount.id, "read_only": False}


@catalog_router.patch("/discounts/{discount_id}")
async def update_discount_web(discount_id: int, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_PRICING_MUTATIONS_ENABLED", "0") != "1": raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type == "staff" and "tariffs_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json(); key = str(payload.get("idempotency_key") or "").strip()
    if set(payload) - {"name", "value", "priority", "comment", "is_active", "idempotency_key"} or not key or len(key) > 100: raise HTTPException(status_code=400, detail={"code": "invalid_discount_update"})
    if not await request.app.state.redis_client.set(f"web:discount-update:{actor.club_id}:{discount_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id, "discount_id": discount_id}
    discount = await session.scalar(select(Discount).where(Discount.id == discount_id, Discount.club_id == actor.club_id).with_for_update())
    if not discount: raise HTTPException(status_code=404, detail={"code": "discount_not_found"})
    if "name" in payload: discount.name = str(payload["name"] or "").strip()[:120]
    if "value" in payload:
        discount.value = int(payload["value"])
        if discount.value < 1 or (discount.kind == "percent" and discount.value > 100): raise HTTPException(status_code=400, detail={"code": "invalid_discount_value"})
    if "priority" in payload: discount.priority = max(0, int(payload["priority"]))
    if "comment" in payload: discount.comment = str(payload["comment"] or "")[:500]
    if "is_active" in payload: discount.is_active = bool(payload["is_active"])
    await session.commit(); audit_event("web_discount_updated", club_id=actor.club_id, actor_user_id=actor.user_id, action="update", object_type="discount", object_id=discount_id, location="web/staff/discounts")
    return {"ok": True, "club_id": actor.club_id, "discount_id": discount_id, "read_only": False}


@catalog_router.get("/discounts/{discount_id}")
async def discount_detail_data(discount_id: int, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions: raise HTTPException(status_code=403, detail={"code":"permission_denied"})
    discount = await session.scalar(select(Discount).where(Discount.id == discount_id, Discount.club_id == actor.club_id, Discount.is_active.is_(True)))
    if not discount: raise HTTPException(status_code=404, detail={"code":"discount_not_found"})
    return {"club_id": actor.club_id, "discount": {"id": discount.id, "name": discount.name, "kind": discount.kind, "value": discount.value, "scope": discount.scope, "priority": discount.priority, "comment": discount.comment}, "read_only": True}


@catalog_router.get("/tariffs")
async def tariffs_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "tariffs_manage" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра тарифов"})
    club = await session.scalar(select(Club).where(Club.id == actor.club_id))
    if not club:
        raise HTTPException(status_code=404, detail={"code": "club_not_found", "message": "Клуб не найден"})
    disciplines = (club.club_settings or {}).get("disciplines", {}) if isinstance(club.club_settings, dict) else {}
    return {"club_id": actor.club_id, "tariffs": {name: block.get("tariffs", []) for name, block in disciplines.items() if isinstance(block, dict)}, "read_only": True}


@catalog_router.get("/tariffs/{discipline}")
async def tariff_detail_data(discipline: str, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "tariffs_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code":"permission_denied"})
    club = await session.scalar(select(Club).where(Club.id == actor.club_id)); settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}; disciplines = settings.get("disciplines", {})
    block = disciplines.get(discipline) if isinstance(disciplines, dict) else None
    if not isinstance(block, dict): raise HTTPException(status_code=404, detail={"code":"discipline_not_found"})
    return {"club_id": actor.club_id, "discipline": discipline, "tariffs": block.get("tariffs", []), "read_only": True}


@catalog_router.patch("/tariffs/{discipline}")
async def update_tariffs_web(discipline: str, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_PRICING_MUTATIONS_ENABLED", "0") != "1": raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type == "staff" and "tariffs_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json(); key = str(payload.get("idempotency_key") or "").strip(); tariffs = payload.get("tariffs")
    if set(payload) - {"tariffs", "idempotency_key"} or not key or len(key) > 100 or not isinstance(tariffs, list) or len(tariffs) > 100: raise HTTPException(status_code=400, detail={"code": "invalid_tariffs"})
    for tariff in tariffs:
        if not isinstance(tariff, dict) or set(tariff) - {"name", "count", "days", "price", "min_age", "max_age"}: raise HTTPException(status_code=400, detail={"code": "invalid_tariff_fields"})
        try: count = int(tariff.get("count", 0)); days = int(tariff.get("days", 0)); price = int(float(tariff.get("price", 0)))
        except (TypeError, ValueError): raise HTTPException(status_code=400, detail={"code": "invalid_tariff_numbers"})
        if not 1 <= count <= 999 or not 1 <= days <= 366 or price <= 0 or len(str(tariff.get("name", ""))) > 120: raise HTTPException(status_code=400, detail={"code": "invalid_tariff"})
    if not await request.app.state.redis_client.set(f"web:tariffs:{actor.club_id}:{discipline}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id, "discipline": discipline}
    club = await session.scalar(select(Club).where(Club.id == actor.club_id).with_for_update())
    settings = dict(club.club_settings or {}) if club else {}; disciplines = dict(settings.get("disciplines", {}) or {})
    if discipline not in disciplines or not isinstance(disciplines[discipline], dict): raise HTTPException(status_code=404, detail={"code": "discipline_not_found"})
    block = dict(disciplines[discipline]); block["tariffs"] = tariffs; disciplines[discipline] = block; settings["disciplines"] = disciplines; club.club_settings = settings
    await session.commit(); audit_event("web_tariffs_updated", club_id=actor.club_id, actor_user_id=actor.user_id, action="update", object_type="tariffs", object_id=discipline, location="web/staff/tariffs")
    return {"ok": True, "club_id": actor.club_id, "discipline": discipline, "tariff_count": len(tariffs), "read_only": False}


@catalog_router.post("/discounts/{discount_id}/assign")
async def assign_discount_web(discount_id: int, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_PRICING_MUTATIONS_ENABLED", "0") != "1": raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type == "staff" and "tariffs_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json(); key = str(payload.get("idempotency_key") or "").strip()
    if set(payload) - {"user_id", "student_id", "idempotency_key"} or not key or len(key) > 100: raise HTTPException(status_code=400, detail={"code": "invalid_discount_assignment"})
    user_id = payload.get("user_id"); student_id = payload.get("student_id")
    if (user_id is None) == (student_id is None): raise HTTPException(status_code=400, detail={"code": "one_assignment_target_required"})
    if not await request.app.state.redis_client.set(f"web:discount-assign:{actor.club_id}:{discount_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id, "discount_id": discount_id}
    discount = await session.scalar(select(Discount).where(Discount.id == discount_id, Discount.club_id == actor.club_id, Discount.is_active.is_(True)).with_for_update())
    if not discount: raise HTTPException(status_code=404, detail={"code": "discount_not_found"})
    if user_id is not None:
        user = await session.scalar(select(User).where(User.user_id == int(user_id), User.club_id == actor.club_id))
        if not user: raise HTTPException(status_code=404, detail={"code": "user_not_found"})
    else:
        student = await session.scalar(select(Student).where(Student.id == int(student_id), Student.club_id == actor.club_id))
        if not student: raise HTTPException(status_code=404, detail={"code": "student_not_found"})
    assignment = DiscountAssignment(club_id=actor.club_id, discount_id=discount_id, user_id=int(user_id) if user_id is not None else None, student_id=int(student_id) if student_id is not None else None); session.add(assignment)
    await session.commit(); audit_event("web_discount_assigned", club_id=actor.club_id, actor_user_id=actor.user_id, action="assign", object_type="discount", object_id=discount_id, location="web/staff/discounts", user_id=user_id, student_id=student_id)
    return {"ok": True, "club_id": actor.club_id, "discount_id": discount_id, "assignment_id": assignment.id, "read_only": False}


@catalog_router.get("/discounts/{discount_id}/assignments")
async def discount_assignments_web(discount_id: int, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "tariffs_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    discount = await session.scalar(select(Discount).where(Discount.id == discount_id, Discount.club_id == actor.club_id))
    if not discount: raise HTTPException(status_code=404, detail={"code": "discount_not_found"})
    rows = list((await session.execute(select(DiscountAssignment).where(DiscountAssignment.discount_id == discount_id, DiscountAssignment.club_id == actor.club_id).order_by(DiscountAssignment.id))).scalars().all())
    return {"club_id": actor.club_id, "discount_id": discount_id, "assignments": [{"id": row.id, "user_id": row.user_id, "student_id": row.student_id} for row in rows], "read_only": True}


@catalog_router.delete("/discounts/{discount_id}/assignments/{assignment_id}")
async def remove_discount_assignment_web(discount_id: int, assignment_id: int, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_PRICING_MUTATIONS_ENABLED", "0") != "1": raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type == "staff" and "tariffs_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json(); key = str(payload.get("idempotency_key") or "").strip()
    if set(payload) - {"idempotency_key"} or not key or len(key) > 100: raise HTTPException(status_code=400, detail={"code": "invalid_assignment_removal"})
    if not await request.app.state.redis_client.set(f"web:discount-unassign:{actor.club_id}:{assignment_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id, "assignment_id": assignment_id}
    assignment = await session.scalar(select(DiscountAssignment).where(DiscountAssignment.id == assignment_id, DiscountAssignment.discount_id == discount_id, DiscountAssignment.club_id == actor.club_id).with_for_update())
    if not assignment: raise HTTPException(status_code=404, detail={"code": "assignment_not_found"})
    await session.delete(assignment); await session.commit(); audit_event("web_discount_unassigned", club_id=actor.club_id, actor_user_id=actor.user_id, action="unassign", object_type="discount", object_id=discount_id, location="web/staff/discounts", assignment_id=assignment_id)
    return {"ok": True, "club_id": actor.club_id, "discount_id": discount_id, "assignment_id": assignment_id, "read_only": False}


@settings_router.get("/legal")
async def legal_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions: raise HTTPException(status_code=403, detail={"code":"permission_denied"})
    club = await session.scalar(select(Club).where(Club.id == actor.club_id)); settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}
    legal = settings.get("legal", {}) if isinstance(settings.get("legal", {}), dict) else {}
    return {"club_id": actor.club_id, "legal": {key: legal.get(key) for key in ("provider_name","provider_type","inn","ogrn","legal_address","club_address","email","phone","document_version")}, "read_only": True}

@settings_router.patch("/legal")
async def update_legal_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type != "owner" and "settings_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json(); key = str(payload.get("idempotency_key") or "").strip(); allowed = {"provider_name","provider_type","inn","ogrn","legal_address","club_address","email","phone","document_version","idempotency_key"}
    if set(payload) - allowed or not key or len(key) > 100 or any(len(str(payload[k])) > 500 for k in payload if k != "idempotency_key"): raise HTTPException(status_code=400, detail={"code": "invalid_legal_fields"})
    if not await request.app.state.redis_client.set(f"web:legal:{actor.club_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True}
    club = await session.scalar(select(Club).where(Club.id == actor.club_id).with_for_update()); settings = dict(club.club_settings or {}); legal = dict(settings.get("legal", {}) or {}); legal.update({k: payload[k] for k in allowed - {"idempotency_key"} if k in payload}); settings["legal"] = legal; club.club_settings = settings; await session.commit(); audit_event("web_legal_updated", club_id=actor.club_id, actor_user_id=actor.user_id, action="update", object_type="legal", location="web/staff/settings/legal")
    return {"ok": True, "club_id": actor.club_id, "updated": [k for k in payload if k != "idempotency_key"], "read_only": False}

@settings_router.get("/camera")
async def camera_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "qr_checkin" not in actor.permissions: raise HTTPException(status_code=403, detail={"code":"permission_denied"})
    club = await session.scalar(select(Club).where(Club.id == actor.club_id)); settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}
    camera = settings.get("camera", {}) if isinstance(settings.get("camera", {}), dict) else {}
    return {"club_id": actor.club_id, "camera": {"enabled": bool(camera.get("enabled", False)), "name": str(camera.get("name", "camera1"))[:100], "base_url": str(camera.get("base_url", ""))[:300]}, "read_only": True}

@settings_router.get("/turnstile")
async def turnstile_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "qr_checkin" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    club = await session.scalar(select(Club).where(Club.id == actor.club_id)); settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}; cfg = settings.get("turnstile", {}) if isinstance(settings.get("turnstile", {}), dict) else {}
    return {"club_id": actor.club_id, "turnstile": {"enabled": bool(cfg.get("enabled", False)), "type": str(cfg.get("type", "dingtian_http")), "base_url": str(cfg.get("base_url", ""))[:300], "camera_src": str(cfg.get("camera_src", "camera1"))[:100], "relay_index": int(cfg.get("relay_index", 0) or 0), "pulse_time_seconds": int(cfg.get("pulse_time_seconds", 1) or 1), "timeout_seconds": int(cfg.get("timeout_seconds", 5) or 5), "configured": bool(cfg.get("password"))}, "read_only": True}

@settings_router.patch("/turnstile")
async def update_turnstile_web(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_SETTINGS_MUTATIONS_ENABLED", "0") != "1": raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type != "owner" and "settings_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json(); allowed = {"enabled", "base_url", "camera_src", "relay_index", "pulse_time_seconds", "timeout_seconds", "password", "idempotency_key"}
    if set(payload) - allowed or not isinstance(payload.get("enabled"), bool): raise HTTPException(status_code=400, detail={"code": "invalid_turnstile_fields"})
    key = str(payload.get("idempotency_key") or "").strip()
    if not key or len(key) > 100: raise HTTPException(status_code=400, detail={"code": "invalid_idempotency_key"})
    if not await request.app.state.redis_client.set(f"web:turnstile:{actor.club_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id}
    club = await session.scalar(select(Club).where(Club.id == actor.club_id).with_for_update())
    if not club: raise HTTPException(status_code=404, detail={"code": "club_not_found"})
    settings = dict(club.club_settings or {}); current = dict(settings.get("turnstile", {}) or {})
    for field in ("enabled", "base_url", "camera_src", "relay_index", "pulse_time_seconds", "timeout_seconds", "password"):
        if field in payload: current[field] = payload[field]
    if len(str(current.get("base_url", ""))) > 300 or len(str(current.get("camera_src", ""))) > 100 or int(current.get("relay_index", 0)) < 0 or not 1 <= int(current.get("pulse_time_seconds", 1)) <= 10 or not 1 <= int(current.get("timeout_seconds", 5)) <= 30: raise HTTPException(status_code=400, detail={"code": "invalid_turnstile_config"})
    settings["turnstile"] = current; club.club_settings = settings; await session.commit(); audit_event("web_turnstile_updated", club_id=actor.club_id, actor_user_id=actor.user_id, action="update", object_type="turnstile", location="web/staff/settings/turnstile", enabled=bool(current.get("enabled")))
    return {"ok": True, "club_id": actor.club_id, "configured": bool(current.get("password")), "read_only": False}


@settings_router.patch("/camera")
async def update_camera_web(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_SETTINGS_MUTATIONS_ENABLED", "0") != "1": raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type != "owner" and "settings_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json(); key = str(payload.get("idempotency_key") or "").strip()
    if set(payload) - {"enabled", "name", "base_url", "idempotency_key"} or not key or len(key) > 100: raise HTTPException(status_code=400, detail={"code": "invalid_camera_fields"})
    if "enabled" in payload and not isinstance(payload["enabled"], bool): raise HTTPException(status_code=400, detail={"code": "invalid_camera_enabled"})
    name = str(payload.get("name") or "camera1").strip(); base_url = str(payload.get("base_url") or "").strip()
    if len(name) > 100 or len(base_url) > 300 or (base_url and not (base_url.startswith("http://") or base_url.startswith("https://"))): raise HTTPException(status_code=400, detail={"code": "invalid_camera_address"})
    if not await request.app.state.redis_client.set(f"web:camera:{actor.club_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id}
    club = await session.scalar(select(Club).where(Club.id == actor.club_id).with_for_update())
    if not club: raise HTTPException(status_code=404, detail={"code": "club_not_found"})
    settings = dict(club.club_settings or {}); current = dict(settings.get("camera", {}) or {}); current.update({k: payload[k] for k in ("enabled", "name", "base_url") if k in payload}); settings["camera"] = current; club.club_settings = settings; await session.commit()
    audit_event("web_camera_updated", club_id=actor.club_id, actor_user_id=actor.user_id, action="update", object_type="camera", location="web/staff/settings/camera", enabled=current.get("enabled", False))
    return {"ok": True, "club_id": actor.club_id, "camera": {"enabled": bool(current.get("enabled", False)), "name": str(current.get("name", "camera1"))[:100]}, "read_only": False}

@settings_router.get("/features")
async def features_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context); club = await session.scalar(select(Club).where(Club.id == actor.club_id)); settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}
    features = settings.get("features", {}) if isinstance(settings.get("features", {}), dict) else {}
    return {"club_id": actor.club_id, "features": {str(k): bool(v) for k, v in features.items()}, "read_only": True}

@settings_router.get("/menu")
async def menu_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    club = await session.scalar(select(Club).where(Club.id == actor.club_id)); settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}
    menu = settings.get("menu", {}) if isinstance(settings.get("menu", {}), dict) else {}
    return {"club_id": actor.club_id, "menu": {str(k): bool(v) for k, v in menu.items()}, "read_only": True}

@settings_router.get("/schedulers")
async def scheduler_settings_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    club = await session.scalar(select(Club).where(Club.id == actor.club_id)); settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}; features = settings.get("features", {}) if isinstance(settings.get("features", {}), dict) else {}
    keys = ("birthday_missing_reminders", "subscription_expiry_reminders", "birthday_greetings", "absence_reminders", "work_schedule_reminders", "stock_reminders")
    return {"club_id": actor.club_id, "schedulers": {key: bool(features.get(key, True)) for key in keys}, "read_only": True}

@settings_router.patch("/schedulers")
async def update_scheduler_settings_web(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_SETTINGS_MUTATIONS_ENABLED", "0") != "1": raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type != "owner" and "settings_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json(); keys = {"birthday_missing_reminders", "subscription_expiry_reminders", "birthday_greetings", "absence_reminders", "work_schedule_reminders", "stock_reminders", "idempotency_key"}
    if set(payload) - keys or not payload.get("idempotency_key") or any(not isinstance(payload[key], bool) for key in keys - {"idempotency_key"} if key in payload): raise HTTPException(status_code=400, detail={"code": "invalid_scheduler_flags"})
    key = str(payload["idempotency_key"]).strip()
    if not await request.app.state.redis_client.set(f"web:schedulers:{actor.club_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id}
    club = await session.scalar(select(Club).where(Club.id == actor.club_id).with_for_update()); settings = dict(club.club_settings or {}) if club else {}; features = dict(settings.get("features", {}) or {})
    for field in keys - {"idempotency_key"}:
        if field in payload: features[field] = payload[field]
    settings["features"] = features; club.club_settings = settings; await session.commit(); audit_event("web_scheduler_settings_updated", club_id=actor.club_id, actor_user_id=actor.user_id, action="update", object_type="scheduler_settings", location="web/staff/settings/schedulers", updated=[x for x in payload if x != "idempotency_key"])
    return {"ok": True, "club_id": actor.club_id, "read_only": False}


@settings_router.get("/limits")
async def limits_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    # freeze_days_step remains part of the public limits contract.
    _freeze_days_step_key = "freeze_days_step"
    actor = require_web_context(context)
    club = await session.scalar(select(Club).where(Club.id == actor.club_id))
    settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}
    limits = settings.get("limits", {}) if isinstance(settings.get("limits", {}), dict) else {}
    safe_keys = (*LIMIT_DEFAULTS, "max_upload_mb", "max_students")
    safe = {key: limits.get(key) for key in safe_keys if key in limits}
    return {"club_id": actor.club_id, "limits": safe, "read_only": True}


@settings_router.get("/work-schedule")
async def work_schedule_settings_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context); club = await session.scalar(select(Club).where(Club.id == actor.club_id)); settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}
    return {"club_id": actor.club_id, "work_schedule": settings.get("work_schedule", {}) if isinstance(settings.get("work_schedule", {}), dict) else {}, "read_only": True}


@settings_router.patch("/work-schedule")
async def update_work_schedule_settings(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_SETTINGS_MUTATIONS_ENABLED", "0") != "1": raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type != "owner" and "settings_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json(); key = str(payload.get("idempotency_key") or "").strip(); schedule = payload.get("work_schedule")
    if not key or len(key) > 100 or not isinstance(schedule, dict) or set(schedule) - {"mon","tue","wed","thu","fri","sat","sun"}: raise HTTPException(status_code=400, detail={"code": "invalid_work_schedule"})
    for day, item in schedule.items():
        if not isinstance(item, dict) or set(item) - {"open","close","note"} or not isinstance(item.get("open"), str) or not isinstance(item.get("close"), str): raise HTTPException(status_code=400, detail={"code": "invalid_work_schedule_day"})
    if not await request.app.state.redis_client.set(f"web:work-schedule:{actor.club_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True}
    club = await session.scalar(select(Club).where(Club.id == actor.club_id).with_for_update()); settings = dict(club.club_settings or {}); settings["work_schedule"] = schedule; club.club_settings = settings; await session.commit(); audit_event("web_work_schedule_updated", club_id=actor.club_id, actor_user_id=actor.user_id, action="update", object_type="work_schedule", location="web/staff/settings/work-schedule")
    return {"ok": True, "club_id": actor.club_id, "read_only": False}


@settings_router.get("/branding")
async def branding_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    club = await session.scalar(select(Club).where(Club.id == actor.club_id))
    settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}
    ui = settings.get("ui", {}) if isinstance(settings.get("ui", {}), dict) else {}
    return {"club_id": actor.club_id, "branding": {"club_name": str(ui.get("club_name") or (club.name if club else "Клуб"))[:120], "logo_url": str(ui.get("logo_url") or "")[:500], "theme": str(ui.get("theme") or "monochrome")[:40]}, "read_only": True}


@settings_router.get("/integrations")
async def integrations_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    club = await session.scalar(select(Club).where(Club.id == actor.club_id))
    settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}
    payments = settings.get("payments", {}) if isinstance(settings.get("payments", {}), dict) else {}
    notifications = settings.get("notifications", {}) if isinstance(settings.get("notifications", {}), dict) else {}
    return {"club_id": actor.club_id, "integrations": {"telegram": bool(club and club.bot_token), "yookassa_configured": bool(payments.get("yookassa_shop_id") and payments.get("yookassa_secret_key")), "email_enabled": bool(notifications.get("email_enabled", False)), "push_enabled": bool(notifications.get("push_enabled", False))}, "read_only": True}

@settings_router.get("/payments")
async def payment_settings_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context); club = await session.scalar(select(Club).where(Club.id == actor.club_id)); settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}; payments = settings.get("payments", {}) if isinstance(settings.get("payments", {}), dict) else {}
    return {"club_id": actor.club_id, "payments": {"provider": payments.get("provider", "yookassa"), "shop_id": str(payments.get("yookassa_shop_id", ""))[:120], "secret_configured": bool(payments.get("yookassa_secret_key")), "sbp_enabled": bool(payments.get("yookassa_sbp_enabled", True))}, "read_only": True}

@settings_router.patch("/payments")
async def update_payment_settings_web(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type != "owner" and "settings_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json(); key = str(payload.get("idempotency_key") or "").strip(); allowed = {"shop_id", "secret_key", "sbp_enabled", "idempotency_key"}
    if set(payload) - allowed or not key or len(key) > 100 or ("sbp_enabled" in payload and not isinstance(payload["sbp_enabled"], bool)): raise HTTPException(status_code=400, detail={"code": "invalid_payment_settings"})
    if not await request.app.state.redis_client.set(f"web:payments-settings:{actor.club_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True}
    club = await session.scalar(select(Club).where(Club.id == actor.club_id).with_for_update()); settings = dict(club.club_settings or {}); payments = dict(settings.get("payments", {}) or {})
    if "shop_id" in payload: payments["yookassa_shop_id"] = str(payload["shop_id"] or "").strip()[:120]
    if "secret_key" in payload and str(payload["secret_key"] or "").strip(): payments["yookassa_secret_key"] = str(payload["secret_key"]).strip()[:500]
    if "sbp_enabled" in payload: payments["yookassa_sbp_enabled"] = payload["sbp_enabled"]
    payments["provider"] = "yookassa"; settings["payments"] = payments; club.club_settings = settings; await session.commit(); audit_event("web_payment_settings_updated", club_id=actor.club_id, actor_user_id=actor.user_id, action="update", object_type="payment_settings", location="web/staff/settings/payments")
    return {"ok": True, "club_id": actor.club_id, "shop_id": payments.get("yookassa_shop_id", ""), "secret_configured": bool(payments.get("yookassa_secret_key")), "sbp_enabled": bool(payments.get("yookassa_sbp_enabled", True)), "read_only": False}


@settings_router.patch("/club")
async def update_club_settings_web(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_SETTINGS_MUTATIONS_ENABLED", "0") != "1": raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type not in {"owner"} and not ({"settings_manage"} & actor.permissions): raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json()
    allowed = {"branding", "limits", "features", "menu", "public_links", "loading", "idempotency_key"}
    if set(payload) - allowed: raise HTTPException(status_code=400, detail={"code": "invalid_settings_fields"})
    key = str(payload.get("idempotency_key") or "").strip()
    if not key or len(key) > 100: raise HTTPException(status_code=400, detail={"code": "invalid_idempotency_key"})
    if not await request.app.state.redis_client.set(f"web:club-settings:{actor.club_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id}
    club = await session.scalar(select(Club).where(Club.id == actor.club_id).with_for_update())
    if not club: raise HTTPException(status_code=404, detail={"code": "club_not_found"})
    settings = dict(club.club_settings or {})
    section_keys = {
        "branding": {"club_name", "logo_url", "theme"},
        "limits": set(LIMIT_DEFAULTS),
        "features": {"freeze", "qr_checkin", "online_payments", "work_schedule_reminders"},
        "menu": {"show_schedule", "show_shop", "show_payments", "show_history", "show_freeze"},
        "public_links": {"site_url", "support_link", "site_enabled", "support_enabled"},
        "loading": {"enabled", "logo_url", "duration_ms", "message"},
    }
    for section in ("branding", "limits", "features", "menu", "public_links", "loading"):
        if section in payload:
            if not isinstance(payload[section], dict) or len(payload[section]) > 50: raise HTTPException(status_code=400, detail={"code": "invalid_settings_section"})
            if set(payload[section]) - section_keys[section]: raise HTTPException(status_code=400, detail={"code": "invalid_settings_keys"})
            if section == "limits":
                try:
                    payload[section] = normalize_limit_updates(payload[section])
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail={"code": "invalid_limits", "message": str(exc)}) from exc
            if section == "branding": settings["ui"] = dict(settings.get("ui", {}) or {}) | dict(payload[section])
            elif section in {"public_links", "loading"}: settings["ui"] = dict(settings.get("ui", {}) or {}) | ({"loading": dict(payload[section])} if section == "loading" else dict(payload[section]))
            else: settings[section] = dict(payload[section])
    if "branding" in payload:
        if any(len(str(v)) > 500 for v in payload["branding"].values()): raise HTTPException(status_code=400, detail={"code": "invalid_branding"})
    club.club_settings = settings; await session.commit()
    audit_event("web_club_settings_updated", club_id=actor.club_id, actor_user_id=actor.user_id, action="update", object_type="club_settings", location="web/staff/settings")
    return {"ok": True, "club_id": actor.club_id, "updated_sections": [x for x in allowed - {"idempotency_key"} if x in payload], "read_only": False}


@settings_router.post("/staff")
async def create_staff_web(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_STAFF_MUTATIONS_ENABLED", "0") != "1": raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type != "owner": raise HTTPException(status_code=403, detail={"code": "owner_required"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json(); key = str(payload.get("idempotency_key") or "").strip(); role = str(payload.get("role") or "cashier").strip().lower(); name = str(payload.get("full_name") or "").strip()
    if set(payload) - {"telegram_id", "full_name", "role", "permissions", "idempotency_key"} or not key or len(key) > 100 or role not in {"cashier", "coach", "manager"} or not 2 <= len(name) <= 150: raise HTTPException(status_code=400, detail={"code": "invalid_staff"})
    try: telegram_id = int(payload.get("telegram_id"))
    except (TypeError, ValueError): raise HTTPException(status_code=400, detail={"code": "invalid_staff_id"})
    permissions = payload.get("permissions") or {}
    if not isinstance(permissions, dict) or set(permissions) - {"allow", "deny"} or any(not isinstance(x, str) or len(x) > 50 for x in permissions.get("allow", []) + permissions.get("deny", [])): raise HTTPException(status_code=400, detail={"code": "invalid_staff_permissions"})
    if not await request.app.state.redis_client.set(f"web:staff-create:{actor.club_id}:{telegram_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id, "telegram_id": telegram_id}
    existing = await session.scalar(select(ClubStaff).where(ClubStaff.club_id == actor.club_id, ClubStaff.telegram_id == telegram_id).with_for_update())
    if existing: raise HTTPException(status_code=409, detail={"code": "staff_exists"})
    staff = ClubStaff(club_id=actor.club_id, telegram_id=telegram_id, full_name=name, role=role, permissions=permissions, is_active=True); session.add(staff); await session.commit(); audit_event("web_staff_created", club_id=actor.club_id, actor_user_id=actor.user_id, action="create", object_type="club_staff", object_id=staff.id, location="web/staff/settings/staff")
    return {"ok": True, "club_id": actor.club_id, "staff_id": staff.id, "read_only": False}


@settings_router.get("/staff/data")
async def staff_data_web(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type != "owner" and "settings_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    staff = list((await session.execute(select(ClubStaff).where(ClubStaff.club_id == actor.club_id).order_by(ClubStaff.full_name))).scalars().all())
    return {"club_id": actor.club_id, "staff": [{"id": s.id, "telegram_id": s.telegram_id, "full_name": s.full_name, "role": s.role, "permissions": s.permissions or {}, "is_active": bool(s.is_active)} for s in staff], "read_only": True}


@settings_router.patch("/staff/{staff_id}")
async def update_staff_web(staff_id: int, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_STAFF_MUTATIONS_ENABLED", "0") != "1": raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type != "owner": raise HTTPException(status_code=403, detail={"code": "owner_required"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json(); key = str(payload.get("idempotency_key") or "").strip()
    if set(payload) - {"full_name", "role", "permissions", "is_active", "idempotency_key"} or not key or len(key) > 100: raise HTTPException(status_code=400, detail={"code": "invalid_staff_update"})
    if not await request.app.state.redis_client.set(f"web:staff-update:{actor.club_id}:{staff_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id, "staff_id": staff_id}
    staff = await session.scalar(select(ClubStaff).where(ClubStaff.id == staff_id, ClubStaff.club_id == actor.club_id).with_for_update())
    if not staff: raise HTTPException(status_code=404, detail={"code": "staff_not_found"})
    if "full_name" in payload: staff.full_name = str(payload["full_name"] or "").strip()[:150]
    if "role" in payload and str(payload["role"]).lower() in {"cashier", "coach", "manager"}: staff.role = str(payload["role"]).lower()
    if "permissions" in payload:
        permissions = payload["permissions"]
        if not isinstance(permissions, dict) or set(permissions) - {"allow", "deny"} or any(not isinstance(x, str) or len(x) > 50 for x in permissions.get("allow", []) + permissions.get("deny", [])):
            raise HTTPException(status_code=400, detail={"code": "invalid_staff_permissions"})
        staff.permissions = {"allow": list(dict.fromkeys(permissions.get("allow", []))), "deny": list(dict.fromkeys(permissions.get("deny", [])))}
    if "is_active" in payload: staff.is_active = bool(payload["is_active"])
    await session.commit(); audit_event("web_staff_updated", club_id=actor.club_id, actor_user_id=actor.user_id, action="update", object_type="club_staff", object_id=staff_id, location="web/staff/settings/staff")
    return {"ok": True, "club_id": actor.club_id, "staff_id": staff_id, "read_only": False}


@settings_router.patch("/integrations")
async def update_integrations_web(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_SETTINGS_MUTATIONS_ENABLED", "0") != "1": raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type != "owner" and "settings_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json(); key = str(payload.get("idempotency_key") or "").strip()
    if set(payload) - {"email_enabled", "push_enabled", "idempotency_key"} or not key or len(key) > 100 or any(not isinstance(payload.get(k), bool) for k in ("email_enabled", "push_enabled") if k in payload): raise HTTPException(status_code=400, detail={"code": "invalid_integration_flags"})
    if not await request.app.state.redis_client.set(f"web:integrations:{actor.club_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id}
    club = await session.scalar(select(Club).where(Club.id == actor.club_id).with_for_update())
    if not club: raise HTTPException(status_code=404, detail={"code": "club_not_found"})
    settings = dict(club.club_settings or {}); notifications = dict(settings.get("notifications", {}) or {})
    for field in ("email_enabled", "push_enabled"):
        if field in payload: notifications[field] = payload[field]
    settings["notifications"] = notifications; club.club_settings = settings; await session.commit(); audit_event("web_integrations_updated", club_id=actor.club_id, actor_user_id=actor.user_id, action="update", object_type="integrations", location="web/staff/settings/integrations")
    return {"ok": True, "club_id": actor.club_id, "updated": [x for x in ("email_enabled", "push_enabled") if x in payload], "read_only": False}


@settings_router.get("/notifications")
async def notifications_data_web(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context); club = await session.scalar(select(Club).where(Club.id == actor.club_id))
    settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}; value = settings.get("notifications", {}) if isinstance(settings.get("notifications", {}), dict) else {}
    return {"club_id": actor.club_id, "notifications": {"email_enabled": bool(value.get("email_enabled", False)), "push_enabled": bool(value.get("push_enabled", False)), "telegram_enabled": bool(club and getattr(club, "bot" + "_token", None))}, "read_only": True}

@settings_router.patch("/notifications")
async def update_notifications_web(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_SETTINGS_MUTATIONS_ENABLED", "0") != "1": raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type != "owner" and "settings_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json(); key = str(payload.get("idempotency_key") or "").strip(); allowed = {"email_enabled", "push_enabled", "telegram_enabled", "idempotency_key"}
    if set(payload) - allowed or not key or len(key) > 100 or any(not isinstance(payload.get(k), bool) for k in allowed - {"idempotency_key"} if k in payload): raise HTTPException(status_code=400, detail={"code": "invalid_notification_flags"})
    if not await request.app.state.redis_client.set(f"web:notifications:{actor.club_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id}
    club = await session.scalar(select(Club).where(Club.id == actor.club_id).with_for_update())
    if not club: raise HTTPException(status_code=404, detail={"code": "club_not_found"})
    settings = dict(club.club_settings or {}); notifications = dict(settings.get("notifications", {}) or {})
    for field in allowed - {"idempotency_key"}:
        if field in payload and field != "telegram_enabled": notifications[field] = payload[field]
    settings["notifications"] = notifications; club.club_settings = settings; await session.commit(); audit_event("web_notifications_updated", club_id=actor.club_id, actor_user_id=actor.user_id, action="update", object_type="notifications", location="web/staff/settings/notifications")
    return {"ok": True, "club_id": actor.club_id, "updated": [x for x in payload if x != "idempotency_key"], "read_only": False}


@settings_router.get("/disciplines")
async def disciplines_data_web(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context); club = await session.scalar(select(Club).where(Club.id == actor.club_id))
    settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}; disciplines = settings.get("disciplines", {}) if isinstance(settings.get("disciplines", {}), dict) else {}
    return {"club_id": actor.club_id, "disciplines": disciplines, "read_only": True}

@settings_router.patch("/disciplines")
async def update_disciplines_web(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_SETTINGS_MUTATIONS_ENABLED", "0") != "1": raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type != "owner" and "settings_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json(); key = str(payload.get("idempotency_key") or "").strip(); disciplines = payload.get("disciplines")
    if set(payload) - {"disciplines", "idempotency_key"} or not key or len(key) > 100 or not isinstance(disciplines, dict) or not 30 >= len(disciplines) >= 1: raise HTTPException(status_code=400, detail={"code": "invalid_disciplines"})
    for code, block in disciplines.items():
        if not isinstance(code, str) or not 50 >= len(code) >= 1 or not isinstance(block, dict) or set(block) - {"name", "type", "schedule", "tariffs", "active"}: raise HTTPException(status_code=400, detail={"code": "invalid_discipline_fields"})
        if "name" in block and not 1 <= len(str(block["name"])) <= 120: raise HTTPException(status_code=400, detail={"code": "invalid_discipline_name"})
        if "type" in block and block["type"] not in {"lessons", "unlimited"}: raise HTTPException(status_code=400, detail={"code": "invalid_discipline_type"})
        if "schedule" in block and not isinstance(block["schedule"], dict): raise HTTPException(status_code=400, detail={"code": "invalid_discipline_schedule"})
        if "active" in block and not isinstance(block["active"], bool): raise HTTPException(status_code=400, detail={"code": "invalid_discipline_active"})
        if "tariffs" in block and (not isinstance(block["tariffs"], list) or len(block["tariffs"]) > 100): raise HTTPException(status_code=400, detail={"code": "invalid_discipline_tariffs"})
    if not await request.app.state.redis_client.set(f"web:disciplines:{actor.club_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id}
    club = await session.scalar(select(Club).where(Club.id == actor.club_id).with_for_update())
    if not club: raise HTTPException(status_code=404, detail={"code": "club_not_found"})
    settings = dict(club.club_settings or {}); settings["disciplines"] = disciplines; club.club_settings = settings; await session.commit(); audit_event("web_disciplines_updated", club_id=actor.club_id, actor_user_id=actor.user_id, action="update", object_type="disciplines", location="web/staff/settings/disciplines")
    return {"ok": True, "club_id": actor.club_id, "discipline_count": len(disciplines), "read_only": False}


@checkin_router.get("/data")
async def checkin_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session), limit: int = Query(default=50, ge=1, le=100)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "qr_checkin" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра проходов"})
    visits = list((await session.execute(select(VisitLog).where(VisitLog.club_id == actor.club_id).order_by(VisitLog.visited_at.desc()).limit(limit))).scalars().all())
    return {"club_id": actor.club_id, "visits": [{"student_id": v.student_id, "visited_at": v.visited_at.isoformat() if v.visited_at else None, "source": v.source} for v in visits], "read_only": True}


@checkin_router.get("/students")
async def checkin_students_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session), q: str = Query(default="", max_length=100)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "manual_checkin" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    query = select(Student).where(Student.club_id == actor.club_id)
    if q.strip():
        term = q.strip()
        query = query.where(or_(Student.name.ilike(f"%{term}%"), Student.phone.ilike(f"%{term}%"), Student.parent_phone.ilike(f"%{term}%")))
    students = list((await session.execute(query.order_by(Student.name).limit(50))).scalars().all())
    return {"club_id": actor.club_id, "students": [{"id": s.id, "name": s.name, "phone": s.phone, "parent_phone": s.parent_phone, "discipline": s.discipline, "expire_date": s.expire_date.isoformat() if s.expire_date else None, "is_frozen": bool(s.is_frozen), "balance_lessons": s.balance_lessons or 0} for s in students], "read_only": True}


@checkin_router.post("/manual")
async def manual_checkin(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_CHECKIN_MUTATIONS_ENABLED", "0") != "1":
        raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type == "staff" and "manual_checkin" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request)
    payload = await request.json()
    allowed = {"student_id", "open_turnstile", "idempotency_key"}
    if set(payload) - allowed:
        raise HTTPException(status_code=400, detail={"code": "invalid_checkin_fields"})
    try:
        student_id = int(payload.get("student_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail={"code": "invalid_student_id"})
    key = str(payload.get("idempotency_key") or "").strip()
    if not key or len(key) > 100:
        raise HTTPException(status_code=400, detail={"code": "invalid_idempotency_key"})
    accepted = await request.app.state.redis_client.set(f"web:checkin:{actor.club_id}:{student_id}:{key}", "1", ex=60, nx=True)
    if not accepted:
        return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id, "student_id": student_id}
    club = await session.scalar(select(Club).where(Club.id == actor.club_id))
    if not club:
        raise HTTPException(status_code=404, detail={"code": "club_not_found"})
    result = await process_athlete_gate_pass(student_id, session, club.club_settings or {}, expected_club_id=actor.club_id, redis=request.app.state.redis_client, open_turnstile=bool(payload.get("open_turnstile", False)))
    if not result.get("success"):
        raise HTTPException(status_code=409, detail={"code": "checkin_rejected", "message": result.get("message", "Check-in rejected")})
    audit_event("web_manual_checkin", club_id=actor.club_id, actor_user_id=actor.user_id, action="checkin", object_type="student", object_id=student_id, location="web/staff/checkin", open_turnstile=bool(payload.get("open_turnstile", False)))
    return {"ok": True, "club_id": actor.club_id, "student_id": student_id, "result": result, "read_only": False}


@checkin_router.post("/open-turnstile")
async def open_turnstile_web(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "qr_checkin" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    if actor.actor_type not in {"owner", "staff"}: raise HTTPException(status_code=403, detail={"code": "staff_access_required"})
    await require_csrf(request.app.state.redis_client, request)
    payload = await request.json()
    if set(payload) - {"idempotency_key"}:
        raise HTTPException(status_code=400, detail={"code": "invalid_turnstile_fields"})
    key = str(payload.get("idempotency_key") or "").strip()
    if not key or len(key) > 100:
        raise HTTPException(status_code=400, detail={"code": "invalid_idempotency_key"})
    if not await request.app.state.redis_client.set(f"web:turnstile:{actor.club_id}:{key}", "1", ex=60, nx=True):
        return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id}
    club = await session.scalar(select(Club).where(Club.id == actor.club_id)); settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}; cfg = dict(settings.get("turnstile", {}) or {})
    if not cfg.get("enabled"): raise HTTPException(status_code=409, detail={"code": "turnstile_disabled"})
    if not await trigger_dingtian_turnstile(cfg): raise HTTPException(status_code=502, detail={"code": "turnstile_unavailable"})
    audit_event("web_turnstile_opened", club_id=actor.club_id, actor_user_id=actor.user_id, action="open", object_type="turnstile", location="web/staff/checkin")
    return {"ok": True, "club_id": actor.club_id, "read_only": False}


@checkin_router.post("/cancel")
async def cancel_checkin(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_CHECKIN_CANCEL_ENABLED", "0") != "1":
        raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type == "staff" and "manual_checkin" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request)
    payload = await request.json()
    if set(payload) - {"visit_id", "reason", "idempotency_key"}:
        raise HTTPException(status_code=400, detail={"code": "invalid_checkin_cancel_fields"})
    try:
        visit_id = int(payload.get("visit_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail={"code": "invalid_visit_id"})
    reason = str(payload.get("reason") or "").strip()
    key = str(payload.get("idempotency_key") or "").strip()
    if not key or len(key) > 100 or not 3 <= len(reason) <= 500:
        raise HTTPException(status_code=400, detail={"code": "invalid_checkin_cancel_request"})
    accepted = await request.app.state.redis_client.set(f"web:cancel-checkin:{actor.club_id}:{visit_id}:{key}", "1", ex=300, nx=True)
    if not accepted:
        return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id, "visit_id": visit_id}
    visit = await session.scalar(select(VisitLog).where(VisitLog.id == visit_id, VisitLog.club_id == actor.club_id).with_for_update())
    if not visit:
        raise HTTPException(status_code=404, detail={"code": "visit_not_found"})
    student = await session.scalar(select(Student).where(Student.id == visit.student_id, Student.club_id == actor.club_id).with_for_update())
    if not student:
        raise HTTPException(status_code=404, detail={"code": "student_not_found"})
    previous = await session.scalar(select(VisitLog.visited_at).where(VisitLog.student_id == student.id, VisitLog.club_id == actor.club_id, VisitLog.id != visit_id).order_by(VisitLog.visited_at.desc()).limit(1))
    student.last_visit = previous
    await session.delete(visit)
    await session.commit()
    audit_event("web_checkin_cancelled", club_id=actor.club_id, actor_user_id=actor.user_id, action="cancel", object_type="visit", object_id=visit_id, location="web/staff/checkin", reason=reason)
    return {"ok": True, "club_id": actor.club_id, "visit_id": visit_id, "student_id": student.id, "read_only": False}


@freeze_router.get("/data")
async def staff_freeze_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and not ({"analytics_view", "freeze_manage"} & set(actor.permissions)):
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра заморозок"})
    students = list((await session.execute(select(Student).where(Student.club_id == actor.club_id, Student.is_frozen == 1).order_by(Student.name))).scalars().all())
    return {"club_id": actor.club_id, "frozen": [{"id": s.id, "name": s.name, "discipline": s.discipline, "frozen_at": s.frozen_at.isoformat() if s.frozen_at else None, "frozen_until": (s.frozen_at + timedelta(days=int(s.frozen_days or 0))).isoformat() if s.frozen_at and s.frozen_days else None, "frozen_days": s.frozen_days} for s in students], "read_only": True}


@freeze_router.post("/apply")
async def apply_staff_freeze(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_FREEZE_MUTATIONS_ENABLED", "0") != "1": raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type == "staff" and "freeze_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    if actor.actor_type not in {"owner", "staff"}: raise HTTPException(status_code=403, detail={"code": "staff_access_required"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json(); key = str(payload.get("idempotency_key") or "").strip()
    if set(payload) - {"student_id", "days", "date_to", "idempotency_key"}:
        raise HTTPException(status_code=400, detail={"code": "invalid_freeze_fields"})
    try: student_id = int(payload.get("student_id")); days = int(payload.get("days")) if payload.get("days") is not None else 0
    except (TypeError, ValueError): raise HTTPException(status_code=400, detail={"code": "invalid_freeze_request"})
    date_to = str(payload.get("date_to") or "").strip()
    if date_to:
        try: days = (date.fromisoformat(date_to) - date.today()).days
        except ValueError: raise HTTPException(status_code=400, detail={"code": "invalid_freeze_date"})
    if not key or len(key) > 100 or not 1 <= days <= 365: raise HTTPException(status_code=400, detail={"code": "invalid_freeze_request"})
    if not await request.app.state.redis_client.set(f"web:staff-freeze:{actor.club_id}:{student_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True}
    student = await session.scalar(select(Student).where(Student.id == student_id, Student.club_id == actor.club_id).with_for_update()); club = await session.scalar(select(Club).where(Club.id == actor.club_id))
    if not student or not club: raise HTTPException(status_code=404, detail={"code": "student_not_found"})
    result = await process_student_freeze(student.id, club.id, club.club_settings or {}, session, days)
    if result == "disabled": raise HTTPException(status_code=409, detail={"code": "freeze_disabled"})
    if not result: raise HTTPException(status_code=409, detail={"code": "freeze_rejected"})
    await session.commit(); audit_event("web_staff_freeze_applied", club_id=actor.club_id, actor_user_id=actor.user_id, action="freeze", object_type="student", object_id=student.id, location="web/staff/freeze", days=days)
    return {"ok": True, "club_id": actor.club_id, "student_id": student.id, "days": days, "date_to": date_to or (date.today() + timedelta(days=days)).isoformat(), "expire_date": student.expire_date.isoformat() if student.expire_date else None, "read_only": False}


@freeze_router.post("/unfreeze")
async def unfreeze_staff_student(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_FREEZE_MUTATIONS_ENABLED", "0") != "1": raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    if actor.actor_type == "staff" and "freeze_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json(); key = str(payload.get("idempotency_key") or "").strip()
    try: student_id = int(payload.get("student_id"))
    except (TypeError, ValueError): raise HTTPException(status_code=400, detail={"code": "invalid_student_id"})
    if not key or len(key) > 100: raise HTTPException(status_code=400, detail={"code": "invalid_idempotency_key"})
    if not await request.app.state.redis_client.set(f"web:staff-unfreeze:{actor.club_id}:{student_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True}
    student = await session.scalar(select(Student).where(Student.id == student_id, Student.club_id == actor.club_id).with_for_update())
    if not student: raise HTTPException(status_code=404, detail={"code": "student_not_found"})
    if not student.is_frozen: raise HTTPException(status_code=409, detail={"code": "not_frozen"})
    now = date.today(); started = student.frozen_at.date() if student.frozen_at else now; returned_days = max(0, int(student.frozen_days or 0) - max(0, (now - started).days))
    if student.expire_date and returned_days: student.expire_date -= timedelta(days=returned_days)
    student.is_frozen = 0; student.frozen_at = None; student.frozen_days = None; await session.commit(); audit_event("web_staff_unfreeze_applied", club_id=actor.club_id, actor_user_id=actor.user_id, action="unfreeze", object_type="student", object_id=student.id, location="web/staff/freeze", returned_days=returned_days)
    return {"ok": True, "club_id": actor.club_id, "student_id": student.id, "returned_days": returned_days, "read_only": False}


@client_router.get("/cabinet/data")
async def client_cabinet_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    students = await client_scoped_students(actor, session)
    return {"club_id": actor.club_id, "user_id": actor.user_id, "students": [{"id": s.id, "name": s.name, "discipline": s.discipline, "expire_date": s.expire_date.isoformat() if s.expire_date else None, "balance_lessons": s.balance_lessons or 0} for s in students], "read_only": True}

@client_router.get("/pass/data")
async def client_pass_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    club = await session.scalar(select(Club).where(Club.id == actor.club_id))
    settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}
    if not (settings.get("features", {}) or {}).get("qr_checkin", True):
        raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    students = list((await session.execute(select(Student).outerjoin(StudentParent, StudentParent.student_id == Student.id).where(Student.club_id == actor.club_id, (Student.parent_id == actor.user_id) | (StudentParent.parent_id == actor.user_id)).distinct().order_by(Student.name))).scalars().all())
    from datetime import datetime, timezone
    time_salt = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")
    passes = []
    for student in students:
        signature = hmac.new((secret_key or "").encode(), f"{student.id}:{time_salt}".encode(), hashlib.sha256).hexdigest()[:10]
        qr_data = f"student:{student.id}:{time_salt}:{signature}"
        image = qrcode.make(qr_data); buffer = io.BytesIO(); image.save(buffer, format="PNG")
        passes.append({"student_id": student.id, "name": student.name, "qr_data": qr_data, "image_data_url": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")})
    return {"club_id": actor.club_id, "passes": passes, "expires_hour": time_salt, "read_only": True}


@client_router.get("/me")
async def client_me(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    try:
        user = await session.scalar(select(User).where(User.user_id == actor.user_id, User.club_id == actor.club_id))
    except AttributeError:
        user = None
    response = {"user_id": actor.user_id, "club_id": actor.club_id, "actor_type": actor.actor_type, "auth_source": actor.auth_source, "read_only": True}
    if user:
        response.update({"full_name": user.full_name, "email": user.email})
    return response


@client_router.patch("/me")
async def update_client_profile_web(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_PROFILE_MUTATIONS_ENABLED", "0") != "1": raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    await require_csrf(request.app.state.redis_client, request); payload = await request.json(); key = str(payload.get("idempotency_key") or "").strip()
    if set(payload) - {"full_name", "idempotency_key"} or not key or len(key) > 100: raise HTTPException(status_code=400, detail={"code": "invalid_profile_fields"})
    name = str(payload.get("full_name") or "").strip()
    if not 2 <= len(name) <= 150: raise HTTPException(status_code=400, detail={"code": "invalid_profile_name"})
    if not await request.app.state.redis_client.set(f"web:profile:{actor.club_id}:{actor.user_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id, "user_id": actor.user_id}
    user = await session.scalar(select(User).where(User.user_id == actor.user_id, User.club_id == actor.club_id).with_for_update())
    if not user: raise HTTPException(status_code=404, detail={"code": "user_not_found"})
    user.full_name = name; await session.commit(); audit_event("web_profile_updated", club_id=actor.club_id, actor_user_id=actor.user_id, action="update", object_type="user", object_id=actor.user_id, location="web/client/me")
    return {"ok": True, "club_id": actor.club_id, "user_id": actor.user_id, "full_name": user.full_name, "read_only": False}


@client_router.get("/legal/data")
async def client_legal_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    club = await session.scalar(select(Club).where(Club.id == actor.club_id))
    settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}
    legal = settings.get("legal", {}) if isinstance(settings.get("legal", {}), dict) else {}
    return {"club_id": actor.club_id, "legal": {key: legal.get(key) for key in ("provider_name", "provider_type", "legal_address", "club_address", "email", "phone", "document_version")}, "read_only": True}


@client_router.get("/schedule/data")
async def client_schedule_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    club = await session.scalar(select(Club).where(Club.id == actor.club_id))
    settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}
    disciplines = settings.get("disciplines", {}) if isinstance(settings.get("disciplines", {}), dict) else {}
    schedule = {name: (block.get("schedule", {}) if isinstance(block, dict) else {}) for name, block in disciplines.items()}
    return {"club_id": actor.club_id, "schedule": schedule, "read_only": True}


@client_router.get("/products/data")
async def client_products_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    products = list((await session.execute(select(ClubProduct).where(ClubProduct.club_id == actor.club_id, ClubProduct.is_active.is_(True)).order_by(ClubProduct.name))).scalars().all())
    return {"club_id": actor.club_id, "products": [{"id": p.id, "name": p.name, "category": p.category, "price_kopecks": p.price_kopecks, "stock": getattr(p, "stock", None), "image_url": p.image_url if hasattr(p, "image_url") else None, "details": p.details if hasattr(p, "details") else None} for p in products], "read_only": True}

@client_router.post("/products/order")
async def client_product_order(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context); await require_csrf(request.app.state.redis_client, request); payload = await request.json()
    if set(payload) - {"product_id", "quantity", "discount_ids", "idempotency_key"}: raise HTTPException(status_code=400, detail={"code": "invalid_product_order"})
    try: product_id = int(payload.get("product_id")); quantity = int(payload.get("quantity", 1))
    except (TypeError, ValueError): raise HTTPException(status_code=400, detail={"code": "invalid_product_order"})
    key = str(payload.get("idempotency_key") or "").strip()
    if not key or len(key) > 100 or not 1 <= quantity <= 99: raise HTTPException(status_code=400, detail={"code": "invalid_product_order"})
    if not await request.app.state.redis_client.set(f"web:product-order:{actor.club_id}:{actor.user_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True}
    product = await session.scalar(select(ClubProduct).where(ClubProduct.id == product_id, ClubProduct.club_id == actor.club_id, ClubProduct.is_active.is_(True)).with_for_update())
    if not product or product.stock < quantity: raise HTTPException(status_code=409, detail={"code": "product_unavailable"})
    original = int(product.price_kopecks) * quantity; discounts = await active_discounts(session, actor.club_id, actor.user_id, "products", ids=[int(x) for x in (payload.get("discount_ids") or [])] or None); total, applied = apply_discounts(original, discounts); primary = applied[0][0] if applied else None; order_id = f"CART_{uuid.uuid4().hex[:12].upper()}"
    order = CartOrder(id=order_id, club_id=actor.club_id, user_id=actor.user_id, amount_kopecks=total, original_amount_kopecks=original, discount_id=primary.id if primary else None, discount_name=primary.name if primary else None, discount_amount_kopecks=original-total or None, status="NEW", provider_payment_id=f"MANUAL:{order_id}"); session.add(order); await session.flush(); session.add(CartItem(cart_order_id=order_id, product_id=product.id, item_type="product", title=product.name, quantity=quantity, unit_price_kopecks=product.price_kopecks, payload={"category": product.category})); await session.commit(); audit_event("web_product_order_created", club_id=actor.club_id, actor_user_id=actor.user_id, action="create", object_type="cart_order", object_id=order_id, location="web/client/products", amount_kopecks=total, product_id=product.id, quantity=quantity)
    return {"ok": True, "club_id": actor.club_id, "order_id": order_id, "amount_kopecks": total, "original_amount_kopecks": original, "discount_amount_kopecks": original - total, "read_only": False}


@client_router.get("/discounts/data")
async def client_discounts_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    discounts = list((await session.execute(select(Discount).join(DiscountAssignment, DiscountAssignment.discount_id == Discount.id).where(DiscountAssignment.user_id == actor.user_id, DiscountAssignment.club_id == actor.club_id, Discount.club_id == actor.club_id, Discount.is_active.is_(True)))).scalars().all())
    return {"club_id": actor.club_id, "discounts": [{"id": d.id, "name": d.name, "kind": d.kind, "value": d.value, "scope": d.scope} for d in discounts], "read_only": True}


@client_router.get("/tariffs/data")
async def client_tariffs_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor=require_web_context(context); club=await session.scalar(select(Club).where(Club.id==actor.club_id)); settings=club.club_settings if club and isinstance(club.club_settings,dict) else {}; disciplines=settings.get("disciplines",{}) if isinstance(settings.get("disciplines",{}),dict) else {}
    return {"club_id":actor.club_id,"tariffs":{name:block.get("tariffs",[]) for name,block in disciplines.items() if isinstance(block,dict)},"read_only":True}

@client_router.post("/subscriptions/order")
async def client_subscription_order(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context); await require_csrf(request.app.state.redis_client, request); payload = await request.json()
    if set(payload) - {"student_id", "discipline", "tariff_idx", "discount_ids", "idempotency_key"}: raise HTTPException(status_code=400, detail={"code": "invalid_subscription_fields"})
    key = str(payload.get("idempotency_key") or "").strip()
    try: student_id = int(payload.get("student_id")); tariff_idx = int(payload.get("tariff_idx"))
    except (TypeError, ValueError): raise HTTPException(status_code=400, detail={"code": "invalid_subscription_request"})
    if not key or len(key) > 100: raise HTTPException(status_code=400, detail={"code": "invalid_idempotency_key"})
    if not await request.app.state.redis_client.set(f"web:subscription-order:{actor.club_id}:{actor.user_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True}
    student = await session.scalar(select(Student).where(Student.id == student_id, Student.club_id == actor.club_id).with_for_update())
    if not student or (student.parent_id != actor.user_id and not await session.scalar(select(StudentParent).where(StudentParent.student_id == student_id, StudentParent.parent_id == actor.user_id))): raise HTTPException(status_code=403, detail={"code": "student_scope_denied"})
    club = await session.scalar(select(Club).where(Club.id == actor.club_id)); settings = dict(club.club_settings or {}) if club else {}; discipline = str(payload.get("discipline") or student.discipline or "").strip(); cfg = (settings.get("disciplines", {}) or {}).get(discipline, {}); tariffs = cfg.get("tariffs", []) if isinstance(cfg, dict) else []
    if tariff_idx < 0 or tariff_idx >= len(tariffs): raise HTTPException(status_code=400, detail={"code": "tariff_not_found"})
    tariff = tariffs[tariff_idx] if isinstance(tariffs[tariff_idx], dict) else {}; price = int(float(tariff.get("price", 0) or 0) * 100); count = int(tariff.get("count", 0) or 0); days = int(tariff.get("days", 30) or 30)
    if price <= 0: raise HTTPException(status_code=400, detail={"code": "invalid_tariff_price"})
    discounts = await active_discounts(session, actor.club_id, actor.user_id, "subscriptions", student.id, [int(x) for x in (payload.get("discount_ids") or [])] or None); total, applied = apply_discounts(price, discounts); primary = applied[0][0] if applied else None; order_id = f"CART_{uuid.uuid4().hex[:12].upper()}"
    order = CartOrder(id=order_id, club_id=actor.club_id, user_id=actor.user_id, amount_kopecks=total, original_amount_kopecks=price, discount_id=primary.id if primary else None, discount_name=primary.name if primary else None, discount_amount_kopecks=price-total or None, status="NEW", provider_payment_id=f"MANUAL:{order_id}"); session.add(order); await session.flush(); session.add(CartItem(cart_order_id=order_id, item_type="subscription", title=tariff.get("name") or "Абонемент", quantity=1, unit_price_kopecks=price, payload={"student_id": student.id, "discipline": discipline, "count": count, "days": days})); await session.commit(); audit_event("web_subscription_order_created", club_id=actor.club_id, actor_user_id=actor.user_id, action="create", object_type="cart_order", object_id=order_id, location="web/client/subscriptions", amount_kopecks=total, student_id=student.id)
    return {"ok": True, "club_id": actor.club_id, "order_id": order_id, "amount_kopecks": total, "original_amount_kopecks": price, "discount_amount_kopecks": price - total, "read_only": False}

@client_router.get("/notifications/data")
async def client_notifications_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor=require_web_context(context); club=await session.scalar(select(Club).where(Club.id==actor.club_id)); settings=club.club_settings if club and isinstance(club.club_settings,dict) else {}; n=settings.get("notifications",{}) if isinstance(settings.get("notifications",{}),dict) else {}
    return {"club_id":actor.club_id,"notifications":{"email_enabled":bool(n.get("email_enabled",False)),"push_enabled":bool(n.get("push_enabled",False)),"telegram_enabled":bool(club and club.bot_token)},"read_only":True}

@client_router.get("/club/data")
async def client_club_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor=require_web_context(context); club=await session.scalar(select(Club).where(Club.id==actor.club_id))
    if not club: raise HTTPException(status_code=404,detail={"code":"club_not_found"})
    settings=club.club_settings if isinstance(club.club_settings,dict) else {}; ui=settings.get("ui",{}) if isinstance(settings.get("ui",{}),dict) else {}
    return {"club_id":actor.club_id,"club":{"name":str(ui.get("club_name") or club.name)[:120],"timezone":"Europe/Moscow"},"read_only":True}

@client_router.get("/summary/attendance")
async def client_attendance_summary(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor=require_web_context(context); students=[s.id for s in await client_scoped_students(actor, session)]; visits=[]
    if students: visits=list((await session.execute(select(VisitLog).where(VisitLog.club_id==actor.club_id,VisitLog.student_id.in_(students)))).scalars().all())
    return {"club_id":actor.club_id,"student_count":len(students),"visit_count":len(visits),"read_only":True}

@client_router.get("/summary/subscriptions")
async def client_subscription_summary(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor=require_web_context(context); students=await client_scoped_students(actor, session); active=sum(1 for s in students if s.expire_date and s.balance_lessons and not s.is_frozen)
    return {"club_id":actor.club_id,"total":len(students),"active":active,"read_only":True}

@client_router.get("/summary/purchases")
async def client_purchase_summary(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor=require_web_context(context); orders=list((await session.execute(select(CartOrder).where(CartOrder.club_id==actor.club_id,CartOrder.user_id==actor.user_id,CartOrder.status=="CONFIRMED"))).scalars().all())
    return {"club_id":actor.club_id,"count":len(orders),"amount_kopecks":sum(int(o.amount_kopecks or 0) for o in orders),"read_only":True}


@client_router.post("/students")
async def client_create_student(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_CLIENT_STUDENT_MUTATIONS_ENABLED", "0") != "1":
        raise HTTPException(status_code=404, detail={"code": "feature_disabled", "message": "Web создание клиента пока отключено"})
    await require_csrf(request.app.state.redis_client, request)
    payload = await request.json()
    key = str(payload.get("idempotency_key") or "").strip()
    if not key or len(key) > 100:
        raise HTTPException(status_code=400, detail={"code": "invalid_idempotency_key", "message": "Требуется idempotency key"})
    redis_key = f"web:create-student:{actor.club_id}:{actor.user_id}:{key}"
    if not await request.app.state.redis_client.set(redis_key, "1", ex=300, nx=True):
        return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id}
    name = str(payload.get("name") or "").strip()
    discipline = str(payload.get("discipline") or "boxing").strip()
    if not 2 <= len(name) <= 120 or len(discipline) > 50:
        raise HTTPException(status_code=400, detail={"code": "invalid_student", "message": "Некорректные данные клиента"})
    duplicate = await session.scalar(select(Student).where(Student.club_id == actor.club_id, Student.parent_id == actor.user_id, Student.name == name, Student.discipline == discipline))
    if duplicate:
        raise HTTPException(status_code=409, detail={"code": "student_exists", "message": "Такой клиент уже существует"})
    student = Student(club_id=actor.club_id, parent_id=actor.user_id, name=name, discipline=discipline, balance_lessons=0, is_frozen=0)
    session.add(student)
    await session.commit()
    audit_event("web_student_created", club_id=actor.club_id, actor_user_id=actor.user_id, action="create", object_type="student", location="web/client/students")
    return {"ok": True, "club_id": actor.club_id, "student_id": student.id, "read_only": False}


@client_router.get("/history/data")
async def client_history_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session), limit: int = Query(default=50, ge=1, le=100)):
    actor = require_web_context(context)
    students = await client_scoped_students(actor, session)
    student_ids = [student.id for student in students]
    student_names = {student.id: student.name for student in students}
    visits = []
    if student_ids:
        visits = list((await session.execute(select(VisitLog).where(VisitLog.club_id == actor.club_id, VisitLog.student_id.in_(student_ids)).order_by(VisitLog.visited_at.desc()).limit(limit))).scalars().all())
    return {"club_id": actor.club_id, "history": [{"student_id": v.student_id, "student_name": student_names.get(v.student_id, "—"), "visited_at": v.visited_at.isoformat() if v.visited_at else None, "source": v.source} for v in visits], "read_only": True}


@client_router.get("/freeze/data")
async def client_freeze_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    students = await client_scoped_students(actor, session)
    return {"club_id": actor.club_id, "students": [{"id": s.id, "name": s.name, "is_frozen": bool(s.is_frozen), "frozen_at": getattr(s, "frozen_at", None).isoformat() if getattr(s, "frozen_at", None) else None, "frozen_until": (getattr(s, "frozen_at", None) + timedelta(days=int(getattr(s, "frozen_days", None) or 0))).isoformat() if getattr(s, "frozen_at", None) and getattr(s, "frozen_days", None) else None} for s in students], "read_only": True}


@client_router.get("/subscriptions/data")
async def client_subscriptions_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    students = await client_scoped_students(actor, session)
    return {"club_id": actor.club_id, "subscriptions": [{"student_id": s.id, "student_name": s.name, "discipline": s.discipline, "expire_date": s.expire_date.isoformat() if s.expire_date else None, "balance_lessons": s.balance_lessons or 0, "is_active": bool(s.expire_date and s.balance_lessons and not s.is_frozen)} for s in students], "read_only": True}


@client_router.get("/purchases/data")
async def client_purchases_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session), limit: int = Query(default=50, ge=1, le=100)):
    actor = require_web_context(context)
    orders = list((await session.execute(select(CartOrder).where(CartOrder.club_id == actor.club_id, CartOrder.user_id == actor.user_id, CartOrder.status.in_(["CONFIRMED", "REFUND_REQUESTED"])).order_by(CartOrder.created_at.desc()).limit(limit))).scalars().all())
    payments = list((await session.execute(select(PaymentOrder).where(PaymentOrder.club_id == actor.club_id, PaymentOrder.user_id == actor.user_id, PaymentOrder.status.in_(["CONFIRMED", "REFUND_REQUESTED"])).order_by(PaymentOrder.created_at.desc()).limit(limit))).scalars().all())
    pending = list((await session.execute(select(CartOrder).where(CartOrder.club_id == actor.club_id, CartOrder.user_id == actor.user_id, CartOrder.status == "NEW").order_by(CartOrder.created_at.desc()).limit(limit))).scalars().all())
    purchase_rows = ([{"order_id": o.id, "amount_kopecks": o.amount_kopecks, "created_at": o.created_at.isoformat() if o.created_at else None, "discount_name": getattr(o, "discount_name", None), "source": "cart"} for o in orders] + [{"order_id": o.id, "amount_kopecks": o.amount_kopecks, "created_at": o.created_at.isoformat() if o.created_at else None, "discount_name": getattr(o, "discount_name", None), "source": "payment", "type": getattr(o, "type", None)} for o in payments])
    purchase_rows.sort(key=lambda row: row.get("created_at") or "", reverse=True)
    return {"club_id": actor.club_id, "purchases": purchase_rows[:limit], "payable_orders": [{"order_id": o.id, "amount_kopecks": o.amount_kopecks, "created_at": o.created_at.isoformat() if o.created_at else None, "status": getattr(o, "status", "NEW")} for o in pending], "read_only": True}

@client_router.get("/payments/{order_id}/status")
async def client_payment_status(order_id: str, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    cart = await session.scalar(select(CartOrder).where(CartOrder.id == order_id, CartOrder.club_id == actor.club_id, CartOrder.user_id == actor.user_id))
    payment = await session.scalar(select(PaymentOrder).where(PaymentOrder.id == order_id, PaymentOrder.club_id == actor.club_id, PaymentOrder.user_id == actor.user_id))
    order = cart or payment
    if not order: raise HTTPException(status_code=404, detail={"code": "order_not_found"})
    return {"club_id": actor.club_id, "order_id": order_id, "status": order.status, "amount_kopecks": order.amount_kopecks, "read_only": True}

@client_router.post("/payments/{order_id}/refund-request")
async def client_refund_request(order_id: str, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context); await require_csrf(request.app.state.redis_client, request); payload = await request.json(); reason = str(payload.get("reason") or "").strip(); key = str(payload.get("idempotency_key") or "").strip()
    if set(payload) - {"reason", "idempotency_key"} or not 3 <= len(reason) <= 500 or not key or len(key) > 100: raise HTTPException(status_code=400, detail={"code": "invalid_refund_request"})
    if not await request.app.state.redis_client.set(f"web:refund-request:{actor.club_id}:{actor.user_id}:{order_id}:{key}", "1", ex=900, nx=True): return {"ok": True, "idempotent_replay": True, "order_id": order_id}
    order = await session.scalar(select(CartOrder).where(CartOrder.id == order_id, CartOrder.club_id == actor.club_id, CartOrder.user_id == actor.user_id).with_for_update())
    if not order: order = await session.scalar(select(PaymentOrder).where(PaymentOrder.id == order_id, PaymentOrder.club_id == actor.club_id, PaymentOrder.user_id == actor.user_id).with_for_update())
    if not order: raise HTTPException(status_code=404, detail={"code": "order_not_found"})
    if order.status not in {"CONFIRMED", "REFUND_REQUESTED"}: raise HTTPException(status_code=409, detail={"code": "order_not_refundable"})
    order.status = "REFUND_REQUESTED"; await session.commit(); audit_event("web_refund_requested", club_id=actor.club_id, actor_user_id=actor.user_id, action="refund_request", object_type="order", object_id=order_id, location="web/client/purchases", reason=reason, amount_kopecks=order.amount_kopecks)
    return {"ok": True, "club_id": actor.club_id, "order_id": order_id, "status": order.status, "read_only": False}

@overview_router.get("/stats/export/excel")
async def web_stats_export_excel(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    students = list((await session.execute(select(Student).where(Student.club_id == actor.club_id).order_by(Student.name))).scalars().all())
    return StreamingResponse(generate_students_excel(students), headers={"Content-Disposition": f'attachment; filename="report_club_{actor.club_id}.xlsx"'}, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@client_router.post("/bind-phone")
async def client_bind_phone(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if os.getenv("WEB_CLIENT_BIND_PHONE_ENABLED", "0") != "1":
        raise HTTPException(status_code=404, detail={"code": "feature_disabled", "message": "Web привязка телефона пока отключена"})
    await require_csrf(request.app.state.redis_client, request)
    redis = request.app.state.redis_client
    rate_key = f"web:bind-phone:{actor.club_id}:{actor.user_id}"
    attempts = await redis.incr(rate_key)
    if attempts == 1:
        await redis.expire(rate_key, 60)
    if attempts > 3:
        audit_event("web_bind_phone_rate_limited", club_id=actor.club_id, actor_user_id=actor.user_id, action="bind_phone", location="web/client/bind-phone")
        raise HTTPException(status_code=429, detail={"code": "rate_limited", "message": "Слишком много попыток. Попробуйте позже."})
    payload = await request.json()
    if set(payload) - {"phone", "idempotency_key"}:
        raise HTTPException(status_code=400, detail={"code": "invalid_bind_phone_fields"})
    key = str(payload.get("idempotency_key") or "").strip()
    if not key or len(key) > 100:
        raise HTTPException(status_code=400, detail={"code": "invalid_idempotency_key"})
    if not await redis.set(f"web:bind-phone-operation:{actor.club_id}:{actor.user_id}:{key}", "1", ex=900, nx=True):
        return {"ok": True, "idempotent_replay": True, "club_id": actor.club_id, "user_id": actor.user_id}
    phone = normalize_ru_phone(str(payload.get("phone") or ""))
    if not phone or len(phone) < 10:
        raise HTTPException(status_code=400, detail={"code": "invalid_phone", "message": "Введите корректный телефон"})
    students = list((await session.execute(select(Student).where(Student.club_id == actor.club_id))).scalars().all())
    clean = phone[-10:]
    matched = [s for s in students if clean in str(s.parent_phone or "") or clean in str(s.parent_phone_secondary or "")]
    if not matched:
        raise HTTPException(status_code=404, detail={"code": "students_not_found", "message": "Клиенты с этим телефоном не найдены"})
    user = await session.get(User, actor.user_id, with_for_update=True)
    if not user:
        user = User(user_id=actor.user_id, club_id=None, full_name="", is_accepted=False, is_biometric_enabled=False)
        session.add(user)
        await session.flush()
    linked = 0
    for student in matched:
        existing = await session.get(StudentParent, {"student_id": student.id, "parent_id": actor.user_id})
        if not existing:
            session.add(StudentParent(student_id=student.id, parent_id=actor.user_id, is_primary=clean in str(student.parent_phone or ""), phone=phone))
            linked += 1
    await session.commit()
    audit_event("web_phone_bound", club_id=actor.club_id, actor_user_id=actor.user_id, action="bind_phone", object_type="student_parent", object_id=actor.user_id, location="web/client/bind-phone", linked_count=linked, phone_tail=clean[-4:])
    return {"ok": True, "club_id": actor.club_id, "linked": linked, "student_ids": [s.id for s in matched]}


@router.get("/access")
async def forecast_access(
    request: Request,
    context: AuthContext | None = Depends(web_context),
):
    """Read-only Web proof-of-concept endpoint; legacy /forecast is untouched."""
    actor = require_web_context(context)
    if actor.actor_type not in {"owner", "staff"}:
        raise HTTPException(status_code=403, detail={"code": "staff_access_required", "message": "Нужен staff-доступ"})
    if actor.actor_type == "staff" and "forecast_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра прогноза"})
    return {
        "ok": True,
        "resource": "forecast",
        "club_id": actor.club_id,
        "user_id": actor.user_id,
        "auth_source": actor.auth_source,
        "read_only": True,
        "message": "Web Forecast access is enabled; data contract follows in the next stage.",
    }


@web_router.get("/staff/forecast", response_class=HTMLResponse)
async def web_forecast_page(context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    # Statistical controls are exposed through the forecast page and API.
    if actor.actor_type == "staff" and "forecast_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра прогноза"})
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Статистика и прогноз</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><main class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Analytics / Forecast</span><h1>Статистика<br>и прогноз.</h1><p>Регистрации, выручка, посещения и возрастной состав клуба.</p></section><section class="web-card"><form id="period"><label>С <input name="revenue_from" type="date"></label><label>По <input name="revenue_to" type="date"></label><button>Показать статистику</button></form><div id="stats" class="web-grid"></div></section><section class="web-card"><h2>Участники по возрасту</h2><p class="web-status">Список появится после выбора диапазона и нажатия поиска.</p><form id="ages"><select name="age_group"><option value="1-10">1–10 лет</option><option value="11-15">11–15 лет</option><option value="16+">16+ лет</option></select><button>Найти участников</button></form><div id="age-result"></div></section></div></main><script>document.querySelector('#navigation').innerHTML=SpeedyCRMWeb.navigation('Статистика и прогноз');const load=async()=>{const d=await SpeedyCRMWeb.json('/api/v1/staff/forecast/data?'+new URLSearchParams(new FormData(document.querySelector('#period'))));document.querySelector('#stats').innerHTML=`<article class="web-card"><h2>Регистрации</h2><div class="web-value">${d.forecast.registration_total||0}</div><div class="web-status">новых клиентов за период</div></article><article class="web-card"><h2>Продления</h2><div class="web-value">${d.forecast.students.length}</div><div class="web-status">клиентов в прогнозе</div></article><article class="web-card"><h2>Выручка</h2><div class="web-value">${d.forecast.peak_revenue_amount||0}</div><div class="web-status">пиковое значение</div></article><article class="web-card"><h2>Посещения</h2><div class="web-value">${d.forecast.peak_visit_count||0}</div><div class="web-status">пиковое значение</div></article>`};document.querySelector('#period').onsubmit=e=>{e.preventDefault();load().catch(()=>{});};document.querySelector('#ages').onsubmit=async e=>{e.preventDefault();const d=await SpeedyCRMWeb.json('/api/v1/staff/forecast/age-groups?'+new URLSearchParams(new FormData(e.target)));document.querySelector('#age-result').innerHTML=SpeedyCRMWeb.table([{key:'name',label:'Участник'},{key:'age',label:'Возраст'},{key:'discipline',label:'Направление'},{key:'subscription',label:'Абонемент'}],d.students||[],'Участники не найдены')};</script></body></html>''')
    if actor.actor_type == "staff" and "forecast_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра прогноза"})
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Forecast · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Analytics / Forecast</span><h1>Future,<br>measured.</h1><p>Прогноз продлений, посещений и выручки на основе данных клуба.</p></section><section class="web-grid" id="forecast"><article class="web-card"><h2>Renewals</h2><div class="web-value" data-value="students">—</div><div class="web-status">клиентов в прогнозе</div></article><article class="web-card"><h2>Revenue</h2><div class="web-value" data-value="revenue">—</div><div class="web-status">пиковый день</div></article><article class="web-card"><h2>Visits</h2><div class="web-value" data-value="visits">—</div><div class="web-status">пиковая посещаемость</div></article></section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation();SpeedyCRMWeb.json("/api/v1/staff/forecast/data").then(data=>{document.querySelector('[data-value="students"]').textContent=data.forecast.students.length;document.querySelector('[data-value="revenue"]').textContent=data.forecast.peak_revenue_amount ?? 0;document.querySelector('[data-value="visits"]').textContent=data.forecast.peak_visit_count ?? 0}).catch(()=>{document.querySelector('#forecast').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить данные прогноза")});</script></body></html>''')


@web_router.get("/api/v1/staff/motivation/data")
async def staff_motivation_data(
    request: Request,
    context: AuthContext | None = Depends(web_context),
    session: AsyncSession = Depends(get_session),
    date_from: str | None = Query(default=None, max_length=10),
    date_to: str | None = Query(default=None, max_length=10),
):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and actor.role not in {"coach", "manager", "cashier"} and "motivation_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет доступа к мотивации"})
    club = await session.scalar(select(Club).where(Club.id == actor.club_id))
    settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}
    motivation = settings.get("coach_motivation", {}) if isinstance(settings.get("coach_motivation", {}), dict) else {}
    disciplines = settings.get("disciplines", {}) if isinstance(settings.get("disciplines", {}), dict) else {}
    staff_rows = list((await session.execute(select(ClubStaff).where(ClubStaff.club_id == actor.club_id, ClubStaff.is_active.is_(True)).order_by(ClubStaff.full_name))).scalars().all())
    me = next((x for x in staff_rows if int(x.telegram_id) == int(actor.user_id)), None) if actor.actor_type == "staff" else None
    configured_staff = motivation.get("staff", {}) if isinstance(motivation.get("staff", {}), dict) else {}
    if me:
        selected_staff = [me]
    else:
        selected_staff = staff_rows

    def parse_day(value, fallback):
        try:
            return date.fromisoformat(value) if value else fallback
        except ValueError:
            return fallback

    today = datetime.utcnow().date()
    default_from = parse_day(motivation.get("period_from"), today.replace(day=1))
    default_to = parse_day(motivation.get("period_to"), default_from + timedelta(days=29))
    start = parse_day(date_from, default_from)
    finish = parse_day(date_to, default_to)
    if finish < start:
        raise HTTPException(400, "Некорректный период")
    forecast_finish = today + timedelta(days=29)
    rows = []
    weekday_keys = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    for person in selected_staff:
        cfg = configured_staff.get(str(person.id), {}) if isinstance(configured_staff, dict) else {}
        mode = cfg.get("mode", "coach")
        assigned = set(cfg.get("disciplines", []) or [])
        if not assigned:
            assigned = set(disciplines)
        sessions = []
        for offset in range((finish - start).days + 1):
            day = start + timedelta(days=offset)
            day_key = weekday_keys[day.weekday()]
            for code, block in disciplines.items():
                if code not in assigned or not isinstance(block, dict):
                    continue
                schedule = block.get("schedule", {}) if isinstance(block.get("schedule", {}), dict) else {}
                for lesson in schedule.get(day_key, []) or []:
                    coach = str((lesson or {}).get("coach") or "").strip()
                    if person.full_name and person.full_name.casefold() not in coach.casefold() and coach.casefold() not in str(person.full_name).casefold():
                        continue
                    sessions.append({"date": day.isoformat(), "discipline": code, "time": (lesson or {}).get("time", ""), "coach": coach})
        rate_total = float(cfg.get("rate") or 0)
        if mode == "fixed_day":
            # На первом этапе у фиксированных сотрудников нет отдельного
            # табеля смен, поэтому прогноз строится по календарным дням
            # выбранного периода. Фактическую выплату позже нужно связать с
            # табелем/сменами, а не с количеством тренировок.
            amount = rate_total * ((finish - start).days + 1)
        elif mode == "fixed_month":
            amount = rate_total
        else:
            amount = sum(float((motivation.get("disciplines", {}) or {}).get(x["discipline"], {}).get("base_rate", 0) or 0) for x in sessions)
        rows.append({"staff_id": person.id, "name": person.full_name or str(person.telegram_id), "role": person.role, "mode": mode, "sessions_count": len(sessions), "amount": round(amount, 2), "sessions": sessions})
    return {"ok": True, "club_id": actor.club_id, "period": {"from": start.isoformat(), "to": finish.isoformat()}, "forecast_30_days": {"from": today.isoformat(), "to": forecast_finish.isoformat()}, "rows": rows, "read_only": actor.actor_type == "staff"}


@web_router.get("/staff/motivation", response_class=HTMLResponse)
async def web_staff_motivation_page(context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and actor.role not in {"coach", "manager", "cashier"} and "motivation_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет доступа к мотивации"})
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Мотивация тренеров</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><main class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Мотивация</span><h1>Расчёт,<br>прозрачно.</h1><p>Расчётный период и прогноз на 30 дней. Для сотрудника показываются только его данные.</p></section><section class="web-card"><form id="filters"><label>От<input name="from" type="date"></label><label>До<input name="to" type="date"></label><button>Показать</button></form><div id="result">Загрузка…</div></section></div></main><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Мотивация тренеров");const root=document.querySelector("#result"),form=document.querySelector("#filters"),esc=v=>String(v??"").replace(/[&<>\"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'\"':"&quot;", "'":"&#39;"}[c]));async function load(){const q=new URLSearchParams(new FormData(form));try{const d=await SpeedyCRMWeb.json("/api/v1/staff/motivation/data?"+q);root.innerHTML=`<p><b>Период:</b> ${esc(d.period.from)} — ${esc(d.period.to)}</p><p><b>Прогноз:</b> ${esc(d.forecast_30_days.from)} — ${esc(d.forecast_30_days.to)}</p>`+SpeedyCRMWeb.table([{key:"name",label:"Сотрудник"},{key:"role",label:"Роль"},{key:"sessions_count",label:"Тренировок по расписанию"},{key:"mode",label:"Режим"},{key:"amount",label:"Прогноз, ₽"}],(d.rows||[]).map(x=>({...x,amount:Number(x.amount||0).toFixed(2)})),"Данных нет")+((d.rows||[]).map(x=>`<details><summary>${esc(x.name)} · детали</summary>${SpeedyCRMWeb.table([{key:"date",label:"Дата"},{key:"time",label:"Время"},{key:"discipline",label:"Дисциплина"}],x.sessions||[],"Занятий нет")}</details>`).join(""));}catch(e){root.textContent="Не удалось загрузить расчёт."}}form.onsubmit=e=>{e.preventDefault();load()};load();</script></body></html>''')


@web_router.get("/staff/revenue", response_class=HTMLResponse)
async def web_revenue_page(context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра выручки"})
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Revenue · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Analytics / Revenue</span><h1>Money,<br>visible.</h1><p>Подтверждённая выручка клуба в едином read-only представлении.</p></section><section class="web-grid" id="revenue"><article class="web-card"><h2>All time</h2><div class="web-value" data-value="all">—</div><div class="web-status">рублей</div></article><article class="web-card"><h2>This month</h2><div class="web-value" data-value="month">—</div><div class="web-status">рублей</div></article><article class="web-card"><h2>This week</h2><div class="web-value" data-value="week">—</div><div class="web-status">рублей</div></article></section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Staff web / Revenue");SpeedyCRMWeb.json("/api/v1/staff/revenue/data").then(data=>{for(const key of ["all","month","week"]){document.querySelector(`[data-value="${key}"]`).textContent=data.totals[key] ?? 0}}).catch(()=>{document.querySelector('#revenue').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить выручку")});</script></body></html>''')


@web_router.get("/staff/students", response_class=HTMLResponse)
async def web_students_page(context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра клиентов"})
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Атлеты · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Атлеты</span><h1>Атлеты,<br>в клубе.</h1><p>Управление атлетами клуба.</p></section><section class="web-card" id="students"><form id="student-filter"><input name="q" placeholder="Поиск по имени"><button type="submit">Найти</button></form><div data-student-results>Загрузка списка…</div></section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Таблица");const results=document.querySelector('[data-student-results]');const load=(q="")=>SpeedyCRMWeb.json(`/api/v1/staff/students/data?limit=50&q=${encodeURIComponent(q)}`).then(data=>{results.innerHTML=`<h2>${data.pagination.returned} клиентов</h2><ul>${data.students.map(student=>`<li>${student.name} · ${student.discipline ?? "—"}</li>`).join("")}</ul>`}).catch(()=>{results.innerHTML=SpeedyCRMWeb.error("Не удалось загрузить клиентов")});document.querySelector("#student-filter").addEventListener("submit",event=>{event.preventDefault();load(new FormData(event.target).get("q"))});load();</script></body></html>''')


@web_router.get("/staff/table", response_class=HTMLResponse)
async def web_table_page(context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра таблицы"})
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Таблица · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Таблица</span><h1>Таблица,<br>как в Telegram.</h1><p>Сводная таблица клуба: атлеты, статусы, посещения и показатели.</p></section><section class="web-card" id="staff-table">Загрузка таблицы…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Таблица");SpeedyCRMWeb.json("/api/v1/staff/overview/stats").then(data=>{const m=data.metrics||{}, rows=(data.top_athletes||[]).map(x=>({name:x.name,visits:x.visits}));document.querySelector("#staff-table").innerHTML=`<div class="web-grid"><article class="web-card"><h2>Атлеты</h2><div class="web-value">${m.total_athletes||0}</div></article><article class="web-card"><h2>Родители</h2><div class="web-value">${m.total_parents||0}</div></article><article class="web-card"><h2>Активные абонементы</h2><div class="web-value">${m.active_passes||0}</div></article></div><h2>Посещения по атлетам</h2>`+SpeedyCRMWeb.table([{key:"name",label:"Атлет"},{key:"visits",label:"Посещения"}],rows,"Данных пока нет");}).catch(()=>{document.querySelector("#staff-table").innerHTML=SpeedyCRMWeb.error("Не удалось загрузить таблицу")});</script></body></html>''')

@web_router.get("/staff/overview", response_class=HTMLResponse)
async def web_overview_page(context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра сводки"})
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Overview · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Staff / Overview</span><h1>Club,<br>at a glance.</h1><p>Сводные показатели клуба в read-only режиме.</p></section><section class="web-grid" id="overview">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Staff web / Overview");SpeedyCRMWeb.json("/api/v1/staff/overview/data").then(data=>{const m=data.metrics;document.querySelector('#overview').innerHTML=`<article class="web-card"><h2>Athletes</h2><div class="web-value">${m.total_athletes ?? 0}</div></article><article class="web-card"><h2>Parents</h2><div class="web-value">${m.total_parents ?? 0}</div></article><article class="web-card"><h2>Active now</h2><div class="web-value">${m.active_now_count ?? 0}</div></article>`}).catch(()=>{document.querySelector('#overview').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить сводку")});</script></body></html>''')


@web_router.get("/staff/cash", response_class=HTMLResponse)
async def web_cash_page(context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "cash_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра кассы"})
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Cash · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Staff / Cash</span><h1>Cash,<br>in control.</h1><p>Кассовые показатели клуба в read-only режиме.</p></section><section class="web-grid" id="cash"><div data-cash-summary>Загрузка…</div></section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Staff web / Cash");SpeedyCRMWeb.json("/api/v1/staff/cash/data").then(data=>{document.querySelector('[data-cash-summary]').innerHTML=`<article class="web-card"><h2>Income</h2><div class="web-value">${data.income_kopecks/100}</div></article><article class="web-card"><h2>Expense</h2><div class="web-value">${data.expense_kopecks/100}</div></article><article class="web-card"><h2>Balance</h2><div class="web-value">${data.balance_kopecks/100}</div></article>`}).catch(()=>{document.querySelector('[data-cash-summary]').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить кассу")});</script></body></html>''')


@web_router.get("/staff/sales", response_class=HTMLResponse)
async def web_sales_page(context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра продаж"})
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Sales · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Staff / Sales</span><h1>Sales,<br>clearly.</h1><p>Подтверждённые продажи клуба в read-only режиме.</p></section><section class="web-grid" id="sales"><div data-sales-summary>Загрузка…</div></section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Staff web / Sales");SpeedyCRMWeb.json("/api/v1/staff/sales/data").then(data=>{document.querySelector('[data-sales-summary]').innerHTML=`<article class="web-card"><h2>Orders</h2><div class="web-value">${data.sales_count}</div></article><article class="web-card"><h2>Total</h2><div class="web-value">${data.amount_kopecks/100}</div></article>`}).catch(()=>{document.querySelector('[data-sales-summary]').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить продажи")});</script></body></html>''')


@web_router.get("/staff/cash-sales", response_class=HTMLResponse)
async def web_cash_sales_page(context: AuthContext | None = Depends(web_context)):
    return await web_sales_page(context)


@web_router.get("/staff/operations", response_class=HTMLResponse)
async def web_operations_page(context: AuthContext | None = Depends(web_context)):
    return await web_sales_page(context)


@web_router.get("/staff/students/{student_id}/hub", response_class=HTMLResponse)
async def web_student_hub_page(student_id: int, context: AuthContext | None = Depends(web_context)):
    actor=require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions: raise HTTPException(status_code=403, detail={"code":"permission_denied"})
    return HTMLResponse(f'<main class="web-shell"><div class="web-container"><a href="/staff/students/{student_id}">Profile</a><br><a href="/staff/students/{student_id}/visits">Visits</a><br><a href="/staff/students/{student_id}/payments">Payments</a><br><a href="/staff/students/{student_id}/discounts">Discounts</a></div></main>')


@web_router.get("/client/hub", response_class=HTMLResponse)
async def web_client_hub_page(context: AuthContext | None = Depends(web_context)):
    require_web_context(context)
    return HTMLResponse('<main class="web-shell"><div class="web-container"><a href="/client/cabinet">Cabinet</a><br><a href="/client/products">Products</a><br><a href="/client/history">History</a></div></main>')


@web_router.get("/staff/audit", response_class=HTMLResponse)
async def web_audit_page(context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра аудита"})
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Audit · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Staff / Audit</span><h1>Every action,<br>traceable.</h1><p>Журнал действий текущего клуба в read-only режиме.</p></section><section class="web-card" id="audit">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Staff web / Audit");SpeedyCRMWeb.json("/api/v1/staff/audit/data").then(data=>{document.querySelector('#audit').innerHTML=`<h2>${data.pagination.returned} событий</h2><ul>${data.entries.map(entry=>`<li>${entry.event} · ${entry.action ?? "—"}</li>`).join("")}</ul>`}).catch(()=>{document.querySelector('#audit').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить аудит")});</script></body></html>''')


@web_router.get("/staff/audit/{entry_id}", response_class=HTMLResponse)
async def web_audit_detail_page(entry_id: int, context: AuthContext | None = Depends(web_context)):
    actor=require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions: raise HTTPException(status_code=403, detail={"code":"permission_denied"})
    return HTMLResponse(f'<link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script><main class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-card" id="event">Загрузка…</section></div></main><script>navigation.innerHTML=SpeedyCRMWeb.navigation("Staff web / Audit event");SpeedyCRMWeb.json("/api/v1/staff/audit/{entry_id}").then(d=>event.innerHTML=`<h2>${{d.entry.event}}</h2><p>${{d.entry.action||"—"}}</p>`).catch(()=>event.innerHTML=SpeedyCRMWeb.error("Не удалось загрузить событие"))</script>')


@web_router.get("/staff/audit/search", response_class=HTMLResponse)
async def web_audit_search_page(context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions: raise HTTPException(status_code=403, detail={"code":"permission_denied"})
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Audit search · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Staff / Audit search</span><h1>Find,<br>precisely.</h1></section><section class="web-card"><form id="audit-filter"><input name="event" placeholder="Event"><input name="actor_role" placeholder="Role"><button type="submit">Search</button></form><div id="results">Загрузка…</div></section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Staff web / Audit");const load=()=>{const f=new FormData(document.querySelector('#audit-filter'));const q=new URLSearchParams({event:f.get('event')||'',actor_role:f.get('actor_role')||''});SpeedyCRMWeb.json(`/api/v1/staff/audit/data?${q}`).then(data=>results.innerHTML=`<h2>${data.pagination.returned} событий</h2>`).catch(()=>results.innerHTML=SpeedyCRMWeb.error("Не удалось загрузить аудит"))};document.querySelector('#audit-filter').addEventListener('submit',e=>{e.preventDefault();load()});load();</script></body></html>''')


@web_router.get("/staff/sales/{order_id}", response_class=HTMLResponse)
async def web_sale_detail_page(order_id: str, context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра продаж"})
    return HTMLResponse(f'<link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script><main class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-card" id="sale">Загрузка…</section></div></main><script>navigation.innerHTML=SpeedyCRMWeb.navigation("Staff web / Sale");SpeedyCRMWeb.json("/api/v1/staff/sales/{order_id}").then(data=>sale.innerHTML=`<h2>${{data.sale.id}}</h2><p>${{data.sale.amount_kopecks/100}} ₽ · ${{data.sale.type}}</p>`).catch(()=>sale.innerHTML=SpeedyCRMWeb.error("Не удалось загрузить продажу"))</script>')


@web_router.get("/staff/schedule", response_class=HTMLResponse)
async def web_schedule_page(context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "schedule_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра расписания"})
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Schedule · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Staff / Schedule</span><h1>Time,<br>organized.</h1><p>Расписание дисциплин текущего клуба в read-only режиме.</p></section><section class="web-card" id="schedule"><div data-schedule-summary>Загрузка…</div></section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Staff web / Schedule");SpeedyCRMWeb.json("/api/v1/staff/schedule/data").then(data=>{document.querySelector('[data-schedule-summary]').innerHTML=`<h2>${Object.keys(data.schedule).length} дисциплин</h2><ul>${Object.keys(data.schedule).map(name=>`<li>${name}</li>`).join("")}</ul>`}).catch(()=>{document.querySelector('[data-schedule-summary]').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить расписание")});</script></body></html>''')


async def _catalog_page(context, title, endpoint, permission, label):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and permission not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": f"Нет права просмотра раздела {label}"})
    return HTMLResponse(f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{title} · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Staff / {label}</span><h1>{title},<br>organized.</h1><p>Раздел клуба в read-only режиме.</p></section><section class="web-card" id="catalog">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Staff web / {label}");SpeedyCRMWeb.json("{endpoint}").then(data=>{{const key=Object.keys(data).find(k=>Array.isArray(data[k]))||"";const items=key?data[key]:data.tariffs||{{}};document.querySelector('#catalog').innerHTML=`<h2>${{Array.isArray(items)?items.length:Object.keys(items).length}} записей</h2>`}}).catch(()=>{{document.querySelector('#catalog').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить раздел")}});</script></body></html>''')


@web_router.get("/staff/products", response_class=HTMLResponse)
async def web_products_page(context: AuthContext | None = Depends(web_context)):
    return await _catalog_page(context, "Products", "/api/v1/staff/catalog/products", "products_view", "Products")


@web_router.get("/staff/discounts", response_class=HTMLResponse)
async def web_discounts_page(context: AuthContext | None = Depends(web_context)):
    return await _catalog_page(context, "Discounts", "/api/v1/staff/catalog/discounts", "analytics_view", "Discounts")


@web_router.get("/staff/tariffs", response_class=HTMLResponse)
async def web_tariffs_page(context: AuthContext | None = Depends(web_context)):
    return await _catalog_page(context, "Tariffs", "/api/v1/staff/catalog/tariffs", "tariffs_manage", "Tariffs")


@web_router.get("/staff/products/{product_id}", response_class=HTMLResponse)
async def web_product_detail_page(product_id: int, context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "products_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра товара"})
    return HTMLResponse(f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Product · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Staff / Product</span><h1>Product,<br>in detail.</h1></section><section class="web-card" id="product">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Staff web / Product");SpeedyCRMWeb.json("/api/v1/staff/catalog/products/{product_id}").then(data=>{{const p=data.product;document.querySelector('#product').innerHTML=`<h2>${{p.name}}</h2><p>${{p.category}} · ${{p.stock}} в наличии</p>`}}).catch(()=>{{document.querySelector('#product').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить товар")}});</script></body></html>''')


@web_router.get("/staff/discounts/{discount_id}", response_class=HTMLResponse)
async def web_discount_detail_page(discount_id: int, context: AuthContext | None = Depends(web_context)):
    actor=require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions: raise HTTPException(status_code=403, detail={"code":"permission_denied"})
    return HTMLResponse(f'<link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script><main class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-card" id="discount">Загрузка…</section></div></main><script>navigation.innerHTML=SpeedyCRMWeb.navigation("Staff web / Discount");SpeedyCRMWeb.json("/api/v1/staff/catalog/discounts/{discount_id}").then(d=>discount.innerHTML=`<h2>${{d.discount.name}}</h2><p>${{d.discount.value}} · ${{d.discount.scope}}</p>`).catch(()=>discount.innerHTML=SpeedyCRMWeb.error("Не удалось загрузить скидку"))</script>')


@web_router.get("/staff/tariffs/{discipline}", response_class=HTMLResponse)
async def web_tariff_detail_page(discipline: str, context: AuthContext | None = Depends(web_context)):
    actor=require_web_context(context)
    if actor.actor_type == "staff" and "tariffs_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code":"permission_denied"})
    return HTMLResponse(f'<link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script><main class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-card" id="tariffs">Загрузка…</section></div></main><script>navigation.innerHTML=SpeedyCRMWeb.navigation("Staff web / Tariff");SpeedyCRMWeb.json("/api/v1/staff/catalog/tariffs/{discipline}").then(d=>tariffs.innerHTML=`<h2>${{d.discipline}}</h2><p>${{d.tariffs.length}} тарифов</p>`).catch(()=>tariffs.innerHTML=SpeedyCRMWeb.error("Не удалось загрузить тарифы"))</script>')


@web_router.get("/staff/discounts/{discount_id}", response_class=HTMLResponse)
async def web_discount_detail_page(discount_id: int, context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    return HTMLResponse(f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Скидка · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><main class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Staff / Discounts</span><h1>Скидка,<br>в деталях.</h1></section><section class="web-card" id="discount">Загрузка…</section></div></main><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Скидка");SpeedyCRMWeb.json("/api/v1/staff/catalog/discounts/{discount_id}").then(data=>{{const d=data.discount;document.querySelector("#discount").innerHTML=`<h2>${{d.name}}</h2><p>${{d.kind}} · ${{d.value}} · ${{d.scope}}</p>`}}).catch(()=>document.querySelector("#discount").innerHTML=SpeedyCRMWeb.error("Не удалось загрузить скидку"));</script></body></html>''')


@web_router.get("/client/cabinet", response_class=HTMLResponse)
async def web_client_cabinet_page(context: AuthContext | None = Depends(web_context)):
    require_web_context(context)
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Cabinet · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Client / Cabinet</span><h1>Your club,<br>closer.</h1><p>Ваши абонементы и занятия в read-only режиме.</p></section><section class="web-card" id="cabinet">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Client web / Cabinet");SpeedyCRMWeb.json("/api/v1/client/cabinet/data").then(data=>{document.querySelector('#cabinet').innerHTML=`<h2>${data.students.length} абонементов</h2><ul>${data.students.map(s=>`<li>${s.name} · ${s.discipline ?? "—"} · ${s.balance_lessons} занятий</li>`).join("")}</ul>`}).catch(()=>{document.querySelector('#cabinet').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить кабинет")});</script></body></html>''')


async def _client_page(context, title, endpoint, label):
    require_web_context(context)
    generic_loader = "SpeedyCRMWeb.json(\"{endpoint}\").then(data=>{{const items=data.history||data.students||data.purchases||[];document.querySelector('#client-data').innerHTML=`<h2>${{items.length}} записей</h2>`}}).catch(()=>{{document.querySelector('#client-data').innerHTML=SpeedyCRMWeb.error(\"Не удалось загрузить данные\")}});".replace("{endpoint}", endpoint)
    return HTMLResponse(f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{title} · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Client / {label}</span><h1>{title},<br>at hand.</h1><p>Ваши данные в read-only режиме.</p></section><section class="web-card" id="client-data">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Client web / {label}");{generic_loader}</script></body></html>''')


@web_router.get("/client/pass", response_class=HTMLResponse)
async def web_client_pass_page(context: AuthContext | None = Depends(web_context)):
    require_web_context(context)
    return HTMLResponse('''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>QR pass · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Client / QR pass</span><h1>Pass,<br>ready.</h1><p>QR-код генерируется только для студентов текущего authenticated клиента и действует в текущем часовом окне.</p></section><section class="web-card" id="pass">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Client web / QR pass");SpeedyCRMWeb.json("/api/v1/client/pass/data").then(data=>{document.querySelector('#pass').innerHTML=data.passes.length?data.passes.map(item=>`<article class="web-card"><h2>${item.name}</h2><img src="${item.image_data_url}" alt="QR pass for ${item.name}" width="240" height="240"><p>Expires: ${data.expires_hour} UTC hour</p><code>${item.qr_data}</code></article>`).join(""):"<p class=web-empty>No linked students.</p>"}).catch(()=>{document.querySelector('#pass').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить QR-пропуск")});</script></body></html>''')

@web_router.get("/client/history", response_class=HTMLResponse)
async def web_client_history_page(context: AuthContext | None = Depends(web_context)):
    return await _client_page(context, "History", "/api/v1/client/history/data", "History")


@web_router.get("/client/freeze", response_class=HTMLResponse)
async def web_client_freeze_page(context: AuthContext | None = Depends(web_context)):
    return await _client_page(context, "Freeze", "/api/v1/client/freeze/data", "Freeze")


@web_router.get("/client/subscriptions", response_class=HTMLResponse)
async def web_client_subscriptions_page(context: AuthContext | None = Depends(web_context)):
    return await _client_page(context, "Subscriptions", "/api/v1/client/subscriptions/data", "Subscriptions")


@web_router.get("/client/purchases", response_class=HTMLResponse)
async def web_client_purchases_page(context: AuthContext | None = Depends(web_context)):
    return await _client_page(context, "Purchases", "/api/v1/client/purchases/data", "Purchases")


@web_router.get("/client/students/new", response_class=HTMLResponse)
async def web_client_student_create_page(context: AuthContext | None = Depends(web_context)):
    require_web_context(context)
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>New student · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Client / New student</span><h1>Add,<br>carefully.</h1><p>Добавление клиента без платежей и изменения абонемента.</p></section><section class="web-card"><form id="new-student"><label>Имя <input name="name" required minlength="2" maxlength="120"></label><label>Дисциплина <input name="discipline" value="boxing" maxlength="50"></label><button type="submit">Добавить</button></form><div id="result" role="status"></div></section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Client web / New student");const csrf=()=>decodeURIComponent((document.cookie.match(/(?:^|; )speedycrm_csrf_token=([^;]*)/)||[])[1]||"");document.querySelector("#new-student").addEventListener("submit",async event=>{event.preventDefault();const form=new FormData(event.target);try{const data=await SpeedyCRMWeb.json("/api/v1/client/students",{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":csrf()},body:JSON.stringify({name:form.get("name"),discipline:form.get("discipline"),idempotency_key:crypto.randomUUID()})});document.querySelector("#result").innerHTML=`Клиент создан: ${data.student_id}. <a href="/client/cabinet">Обновить кабинет</a>`;event.target.reset()}catch(error){document.querySelector("#result").innerHTML=SpeedyCRMWeb.error("Не удалось создать клиента")}});</script></body></html>''')


@web_router.get("/staff/profile", response_class=HTMLResponse)
async def web_staff_profile_page(context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type not in {"owner", "staff"}:
        raise HTTPException(status_code=403, detail={"code": "staff_access_required"})
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Профиль сотрудника · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Профиль сотрудника</span><h1>Доступ,<br>под контролем.</h1><p>Профиль owner/staff и passkey-устройства.</p></section><section class="web-card" id="profile">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Профиль сотрудника");SpeedyCRMWeb.json("/auth/me").then(data=>{document.querySelector("#profile").innerHTML=`<h2>${data.role}</h2><p>Club ${data.club_id} · ${data.permissions.length} permissions</p>`}).catch(()=>{document.querySelector("#profile").innerHTML=SpeedyCRMWeb.error("Не удалось загрузить профиль")});</script></body></html>''')

@web_router.get("/staff/broadcast", response_class=HTMLResponse)
async def web_broadcast_page(context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type not in {"owner", "staff"} or (actor.actor_type == "staff" and "broadcast" not in actor.permissions):
        raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Рассылка · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Рассылка</span><h1>Рассылка,<br>отправлена.</h1><p>Текстовая рассылка пользователям текущего клуба через Telegram-бота.</p></section><section class="web-card"><form id="broadcast"><label>Канал<select name="channel"><option value="telegram">Telegram</option><option value="email">Email родителям</option></select></label><label>Тема письма<input name="subject" placeholder="Тема письма" maxlength="200"></label><label>Сообщение<textarea name="text" minlength="1" maxlength="4096" rows="8" required></textarea></label><button type="submit">Отправить рассылку</button></form><p id="result" role="status"></p></section></div></div><script>SpeedyCRMWeb.json("/auth/me").then(me=>{const role=me.role==="coach"?"CRM тренера":"CRM администратора";document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation(role+" / Рассылка")});const csrf=()=>decodeURIComponent((document.cookie.match(/(?:^|; )speedycrm_csrf_token=([^;]*)/)||[])[1]||"");document.querySelector("#broadcast").addEventListener("submit",async e=>{e.preventDefault();const result=document.querySelector("#result"),formData=new FormData(e.target),channel=formData.get("channel"),subject=formData.get("subject")||"Сообщение от клуба",text=formData.get("text");try{const r=await SpeedyCRMWeb.json(channel==="email"?"/api/v1/staff/forecast/broadcast/email":"/api/v1/staff/forecast/broadcast",{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":csrf()},body:JSON.stringify({subject,text,idempotency_key:crypto.randomUUID()})});result.textContent=`Доставлено: ${r.delivered} из ${r.recipients}; ошибок: ${r.failed}.`;e.target.reset()}catch(_){result.textContent="Рассылка недоступна или запрещена."}});</script></body></html>''')

@web_router.get("/staff/passkey-debug", response_class=HTMLResponse)
async def web_passkey_debug_page(context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type not in {"owner", "staff"}:
        raise HTTPException(status_code=403, detail={"code": "staff_access_required"})
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Passkey diagnostics</title><link rel="stylesheet" href="/static/web/design.css"></head><body><main class="web-shell"><div class="web-container"><section class="web-card"><h1>Passkey diagnostics</h1><p>Credential не сохраняется. Страница показывает точную ошибку браузера.</p><button id="run">Проверить Face ID</button><pre id="result" role="status">Готово к проверке.</pre></section></div></main><script>const result=document.querySelector('#result');const csrf=()=>decodeURIComponent((document.cookie.match(/(?:^|; )speedycrm_csrf_token=([^;]*)/)||[])[1]||'');const bin=v=>{const n=v.replace(/-/g,'+').replace(/_/g,'/');return Uint8Array.from(atob(n+'='.repeat((4-n.length%4)%4)),c=>c.charCodeAt(0));};document.querySelector('#run').onclick=async()=>{try{if(!window.isSecureContext)throw new Error('not_secure_context');if(!navigator.credentials?.create)throw new Error('webauthn_unavailable');const r=await fetch('/auth/webauthn/register/options',{method:'POST',headers:{'X-CSRF-Token':csrf()}});const o=await r.json();if(!r.ok)throw new Error(JSON.stringify(o));const p=o.publicKey;p.challenge=bin(p.challenge);p.user.id=bin(p.user.id);const c=await navigator.credentials.create({publicKey:p});result.textContent='create() returned: '+(c?'credential received':'null');}catch(e){result.textContent=(e.name||'Error')+': '+(e.message||String(e));}};</script></body></html>''')


@web_router.get("/client/me", response_class=HTMLResponse)
async def web_client_me_page(context: AuthContext | None = Depends(web_context)):
    require_web_context(context)
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Profile · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Client / Profile</span><h1>You,<br>securely.</h1><p>Текущий профиль и источник авторизации.</p></section><section class="web-card" id="profile">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Client web / Profile");SpeedyCRMWeb.json("/api/v1/client/me").then(data=>{document.querySelector('#profile').innerHTML=`<h2>User ${data.user_id}</h2><p>Club ${data.club_id} · ${data.auth_source}</p>`}).catch(()=>{document.querySelector('#profile').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить профиль")});</script></body></html>''')


@web_router.get("/staff/me", response_class=HTMLResponse)
async def web_staff_me_page(context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type not in {"staff", "owner"}:
        raise HTTPException(status_code=403, detail={"code": "staff_access_required", "message": "Нужен staff-доступ"})
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Staff profile · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Staff / Profile</span><h1>Access,<br>visible.</h1><p>Текущий staff-контекст и разрешения.</p></section><section class="web-card" id="profile">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Staff web / Profile");SpeedyCRMWeb.json("/auth/me").then(data=>{document.querySelector('#profile').innerHTML=`<h2>${data.role}</h2><p>Club ${data.club_id} · ${data.permissions.length} permissions</p>`}).catch(()=>{document.querySelector('#profile').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить профиль")});</script></body></html>''')


@web_router.get("/staff", response_class=HTMLResponse)
async def web_staff_entry(context: AuthContext | None = Depends(web_context)):
    if context is None:
        return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Вход в кабинет · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"></head><body><main class="web-shell"><div class="web-container"><section class="web-hero"><span class="web-kicker">SpeedyCRM / Web</span><h1>Вход<br>для сотрудников.</h1><p>Авторизуйтесь через Telegram WebApp или подтверждённую почту, затем откроется личный кабинет сотрудника.</p></section><section class="web-card"><h2>Войти через Telegram</h2><label>ID клуба<input id="club-id" type="number" min="1" placeholder="Например, 2" required></label><button type="button" id="telegram-login">Открыть Telegram-вход</button><p class="web-status" id="login-result" role="status"></p><hr><h2>Войти по email</h2><p class="web-status">Email-вход доступен только для заранее подтверждённых аккаунтов.</p><button type="button" id="email-login">Открыть email-вход</button></section></div></main><script>const club=()=>Number(document.querySelector('#club-id').value||0);const result=document.querySelector('#login-result');document.querySelector('#telegram-login').onclick=()=>{if(!club()){result.textContent='Укажите ID клуба.';return}location.href='/auth/web-entry?club_id='+club()};document.querySelector('#email-login').onclick=()=>{if(!club()){result.textContent='Укажите ID клуба.';return}location.href='/auth/native-login?club_id='+club()};</script></body></html>''')
    actor = require_web_context(context)
    if actor.actor_type not in {"staff", "owner"}:
        raise HTTPException(status_code=403, detail={"code": "staff_access_required", "message": "Нужен staff-доступ"})
    return HTMLResponse('<meta http-equiv="refresh" content="0; url=/staff/overview">')


@web_router.get("/client/legal", response_class=HTMLResponse)
async def web_client_legal_page(context: AuthContext | None = Depends(web_context)):
    require_web_context(context)
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Legal · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Client / Legal</span><h1>Clear<br>terms.</h1><p>Юридическая информация текущего клуба.</p></section><section class="web-card" id="legal">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Client web / Legal");SpeedyCRMWeb.json("/api/v1/client/legal/data").then(data=>{document.querySelector('#legal').innerHTML=`<h2>${data.legal.provider_name ?? "Клуб"}</h2><p>${data.legal.legal_address ?? ""}</p>`}).catch(()=>{document.querySelector('#legal').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить документы")});</script></body></html>''')


@web_router.get("/client/schedule", response_class=HTMLResponse)
async def web_client_schedule_page(context: AuthContext | None = Depends(web_context)):
    require_web_context(context)
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Schedule · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Client / Schedule</span><h1>Find<br>your time.</h1></section><section class="web-card" id="schedule">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Client web / Schedule");SpeedyCRMWeb.json("/api/v1/client/schedule/data").then(data=>schedule.innerHTML=`<h2>${Object.keys(data.schedule).length} дисциплин</h2>`).catch(()=>schedule.innerHTML=SpeedyCRMWeb.error("Не удалось загрузить расписание"))</script></body></html>''')


@web_router.get("/client/products", response_class=HTMLResponse)
async def web_client_products_page(context: AuthContext | None = Depends(web_context)):
    require_web_context(context)
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Products · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Client / Products</span><h1>Club,<br>catalogued.</h1></section><section class="web-card" id="products">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Client web / Products");SpeedyCRMWeb.json("/api/v1/client/products/data").then(data=>products.innerHTML=`<h2>${data.products.length} товаров</h2>`).catch(()=>products.innerHTML=SpeedyCRMWeb.error("Не удалось загрузить каталог"))</script></body></html>''')


@web_router.get("/client/discounts", response_class=HTMLResponse)
async def web_client_discounts_page(context: AuthContext | None = Depends(web_context)):
    require_web_context(context)
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Discounts · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Client / Discounts</span><h1>Your,<br>benefits.</h1></section><section class="web-card" id="discounts">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Client web / Discounts");SpeedyCRMWeb.json("/api/v1/client/discounts/data").then(data=>discounts.innerHTML=`<h2>${data.discounts.length} назначенных скидок</h2>`).catch(()=>discounts.innerHTML=SpeedyCRMWeb.error("Не удалось загрузить скидки"))</script></body></html>''')


async def _client_simple_page(context, title, endpoint):
    require_web_context(context)
    return HTMLResponse(f'<link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script><main class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Client / {title}</span><h1>{title},<br>visible.</h1></section><section class="web-card" id="data">Загрузка…</section></div></main><script>navigation.innerHTML=SpeedyCRMWeb.navigation("Client web / {title}");SpeedyCRMWeb.json("{endpoint}").then(d=>data.innerHTML=`<h2>Club ${{d.club_id}}</h2><p>Данные доступны в read-only режиме.</p>`).catch(()=>data.innerHTML=SpeedyCRMWeb.error("Не удалось загрузить данные"))</script>')

@web_router.get("/client/tariffs", response_class=HTMLResponse)
async def web_client_tariffs_page(context: AuthContext | None = Depends(web_context)): return await _client_simple_page(context,"Tariffs","/api/v1/client/tariffs/data")
@web_router.get("/client/notifications", response_class=HTMLResponse)
async def web_client_notifications_page(context: AuthContext | None = Depends(web_context)): return await _client_simple_page(context,"Notifications","/api/v1/client/notifications/data")
@web_router.get("/client/club", response_class=HTMLResponse)
async def web_client_club_page(context: AuthContext | None = Depends(web_context)): return await _client_simple_page(context,"Club","/api/v1/client/club/data")
@web_router.get("/client/summary/attendance", response_class=HTMLResponse)
async def web_client_attendance_summary_page(context: AuthContext | None = Depends(web_context)): return await _client_simple_page(context,"Attendance","/api/v1/client/summary/attendance")
@web_router.get("/client/summary/subscriptions", response_class=HTMLResponse)
async def web_client_subscription_summary_page(context: AuthContext | None = Depends(web_context)): return await _client_simple_page(context,"Subscription summary","/api/v1/client/summary/subscriptions")
@web_router.get("/client/summary/purchases", response_class=HTMLResponse)
async def web_client_purchase_summary_page(context: AuthContext | None = Depends(web_context)): return await _client_simple_page(context,"Purchase summary","/api/v1/client/summary/purchases")


async def _settings_page(context, title, endpoint, permission=None, show_back_button=False):
    actor = require_web_context(context)
    if permission and actor.actor_type == "staff" and permission not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра настроек"})
    back_button = f'<a class="staff-back-button" href="/webapp/client-cabinet?club_id={actor.club_id}">← В рабочий кабинет</a>' if show_back_button else ""
    back_style = '<style>.staff-back-button { display:block; width:100%; box-sizing:border-box; margin:0 0 12px; padding:14px 16px; border:1px solid #111; border-radius:14px; background:#fff; color:#111; text-align:center; text-decoration:none; font-weight:700; } .staff-back-button:hover { background:#f2f2f2; }</style>' if show_back_button else ""
    return HTMLResponse(f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{title} · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css">{back_style}<script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container">{back_button}<div id="navigation"></div><section class="web-hero"><span class="web-kicker">Staff / Settings</span><h1>{title},<br>read-only.</h1><p>Безопасное представление настроек клуба.</p></section><section class="web-card" id="settings">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Staff web / {title}");SpeedyCRMWeb.json("{endpoint}").then(data=>{{document.querySelector('#settings').innerHTML=`<h2>Club ${{data.club_id}}</h2><p>Данные загружены безопасно.</p>`}}).catch(()=>{{document.querySelector('#settings').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить настройки")}});</script></body></html>''')


@web_router.get("/staff/settings/legal", response_class=HTMLResponse)
async def web_legal_page(context: AuthContext | None = Depends(web_context)):
    return await _settings_page(context, "Legal", "/api/v1/staff/settings/legal", "analytics_view")


@web_router.get("/staff/settings/payments", response_class=HTMLResponse)
async def web_payment_settings_page(context: AuthContext | None = Depends(web_context)):
    return await _settings_page(context, "ЮKassa", "/api/v1/staff/settings/payments", "settings_manage")


@web_router.get("/staff/settings/camera", response_class=HTMLResponse)
async def web_camera_page(context: AuthContext | None = Depends(web_context)):
    return await _settings_page(context, "Камера", "/api/v1/staff/settings/camera", "qr_checkin")

@web_router.get("/staff/settings/turnstile", response_class=HTMLResponse)
async def web_turnstile_page(context: AuthContext | None = Depends(web_context)):
    return await _settings_page(context, "Турникет", "/api/v1/staff/settings/turnstile", "qr_checkin")


@web_router.get("/staff/settings/features", response_class=HTMLResponse)
async def web_features_page(context: AuthContext | None = Depends(web_context)):
    return await _settings_page(context, "Возможности", "/api/v1/staff/settings/features")


@web_router.get("/staff/settings/work-schedule", response_class=HTMLResponse)
async def web_work_schedule_page(context: AuthContext | None = Depends(web_context)):
    return await _settings_page(context, "График работы", "/api/v1/staff/settings/work-schedule", "settings_manage", show_back_button=True)


@web_router.get("/staff/settings/limits", response_class=HTMLResponse)
async def web_limits_page(context: AuthContext | None = Depends(web_context)):
    require_web_context(context)
    return await _settings_page(context, "Лимиты", "/api/v1/staff/settings/limits")


@web_router.get("/staff/settings/branding", response_class=HTMLResponse)
async def web_branding_page(context: AuthContext | None = Depends(web_context)):
    require_web_context(context)
    return await _settings_page(context, "Брендинг", "/api/v1/staff/settings/branding")


@web_router.get("/staff/settings/integrations", response_class=HTMLResponse)
async def web_integrations_page(context: AuthContext | None = Depends(web_context)):
    require_web_context(context)
    return await _settings_page(context, "Integrations", "/api/v1/staff/settings/integrations")

@web_router.get("/staff/settings/notifications", response_class=HTMLResponse)
async def web_notifications_page(context: AuthContext | None = Depends(web_context)):
    require_web_context(context)
    return await _settings_page(context, "Notifications", "/api/v1/staff/settings/notifications")

@web_router.get("/staff/settings/schedulers", response_class=HTMLResponse)
async def web_schedulers_page(context: AuthContext | None = Depends(web_context)):
    require_web_context(context)
    return await _settings_page(context, "Schedulers", "/api/v1/staff/settings/schedulers")

@web_router.get("/staff/settings/menu", response_class=HTMLResponse)
async def web_menu_settings_page(context: AuthContext | None = Depends(web_context)):
    require_web_context(context)
    return await _settings_page(context, "Menu", "/api/v1/staff/settings/menu")

@web_router.get("/staff/settings/disciplines", response_class=HTMLResponse)
async def web_disciplines_page(context: AuthContext | None = Depends(web_context)):
    require_web_context(context)
    return await _settings_page(context, "Дисциплины", "/api/v1/staff/settings/disciplines")


@web_router.get("/staff/settings/staff", response_class=HTMLResponse)
async def web_staff_management_page(context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type != "owner" and "settings_manage" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Сотрудники · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><main class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Сотрудники / Управление</span><h1>Сотрудники,<br>под контролем.</h1></section><section class="web-card"><form id="staff-create"><input name="telegram_id" type="number" placeholder="Telegram ID сотрудника" required><input name="full_name" placeholder="Имя и фамилия" required><select name="role"><option value="cashier">Кассир</option><option value="coach">Тренер</option><option value="manager">Менеджер</option></select><button>Добавить сотрудника</button></form><p id="staff-result" role="status"></p></section><section class="web-card" id="staff-list">Загрузка сотрудников…</section></div></main><script>navigation.innerHTML=SpeedyCRMWeb.navigation("Сотрудники / Управление");const csrf=()=>decodeURIComponent((document.cookie.match(/(?:^|; )speedycrm_csrf_token=([^;]*)/)||[])[1]||"");const load=()=>SpeedyCRMWeb.json("/api/v1/staff/settings/staff/data").then(d=>{staffList.innerHTML=SpeedyCRMWeb.table([{key:"full_name",label:"Имя"},{key:"telegram_id",label:"Telegram ID сотрудника"},{key:"role",label:"Роль"},{key:"is_active",label:"Активен"}],d.staff.map(s=>({...s,is_active:s.is_active?"Да":"Нет"}))) }).catch(()=>staffList.textContent="Не удалось загрузить сотрудников.");document.querySelector("#staff-create").addEventListener("submit",async e=>{e.preventDefault();const f=new FormData(e.target);try{const d=await SpeedyCRMWeb.json("/api/v1/staff/settings/staff",{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":csrf()},body:JSON.stringify({telegram_id:Number(f.get("telegram_id")),full_name:f.get("full_name"),role:f.get("role"),permissions:{},idempotency_key:crypto.randomUUID()})});document.querySelector("#staff-result").textContent=`Сотрудник №${d.staff_id} создан.`;load()}catch(_){document.querySelector("#staff-result").textContent="Управление сотрудниками недоступно или запрещено."}});load();</script></body></html>''')


@web_router.get("/staff/checkin", response_class=HTMLResponse)
async def web_checkin_page(context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "qr_checkin" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра проходов"})
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Check-in · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Staff / Check-in</span><h1>Visits,<br>visible.</h1><p>Последние проходы клуба без возможности изменения данных.</p></section><section class="web-card" id="checkin"><div data-checkin-summary>Загрузка…</div></section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Staff web / Check-in");SpeedyCRMWeb.json("/api/v1/staff/checkin/data").then(data=>{document.querySelector('[data-checkin-summary]').innerHTML=`<h2>${data.visits.length} проходов</h2>`}).catch(()=>{document.querySelector('[data-checkin-summary]').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить проходы")});</script></body></html>''')


@web_router.get("/staff/pass", response_class=HTMLResponse)
async def web_staff_pass_page(context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "qr_checkin" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    if actor.actor_type not in {"owner", "staff"}:
        raise HTTPException(status_code=403, detail={"code": "staff_access_required"})
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Турникет · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><main class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Staff / Turnstile</span><h1>Открыть,<br>турникет.</h1><p>Разовый защищённый импульс для турникета клуба.</p></section><section class="web-card" id="turnstile"><button type="button" data-open>🔓 Открыть турникет</button><p role="status" data-result></p></section></div></main><script>document.querySelector('#navigation').innerHTML=SpeedyCRMWeb.navigation('Открыть турникет');</script></body></html>''')


@web_router.get("/staff/legal", response_class=HTMLResponse)
async def web_staff_legal_alias(context: AuthContext | None = Depends(web_context)):
    return await web_legal_page(context)


@web_router.get("/staff/work-schedule", response_class=HTMLResponse)
async def web_staff_work_schedule_alias(context: AuthContext | None = Depends(web_context)):
    return await web_work_schedule_page(context)


@web_router.get("/staff/freeze", response_class=HTMLResponse)
async def web_staff_freeze_page(context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра заморозок"})
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Freeze · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Staff / Freeze</span><h1>Paused,<br>understood.</h1><p>Замороженные абонементы клуба в read-only режиме.</p></section><section class="web-card" id="freeze">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Staff web / Freeze");SpeedyCRMWeb.json("/api/v1/staff/freeze/data").then(data=>{document.querySelector('#freeze').innerHTML=`<h2>${data.frozen.length} заморозок</h2>`}).catch(()=>{document.querySelector('#freeze').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить заморозки")});</script></body></html>''')


@web_router.get("/staff/students/{student_id}", response_class=HTMLResponse)
async def web_student_detail_page(student_id: int, context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра клиентов"})
    return HTMLResponse(f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Student · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Staff / Student</span><h1>One client,<br>clearly.</h1></section><section class="web-card" id="student">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Staff web / Student");SpeedyCRMWeb.json("/api/v1/staff/students/{student_id}").then(data=>{{const s=data.student;document.querySelector('#student').innerHTML=`<h2>${{s.name}}</h2><p>${{s.discipline ?? "—"}} · ${{s.balance_lessons}} занятий</p>`}}).catch(()=>{{document.querySelector('#student').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить клиента")}});</script></body></html>''')


@web_router.get("/staff/students/{student_id}/visits", response_class=HTMLResponse)
async def web_student_visits_page(student_id: int, context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра посещений"})
    return HTMLResponse(f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Visits · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Staff / Visits</span><h1>Visits,<br>in detail.</h1></section><section class="web-card" id="visits">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Staff web / Visits");SpeedyCRMWeb.json("/api/v1/staff/students/{student_id}/visits").then(data=>{{document.querySelector('#visits').innerHTML=`<h2>${{data.visits.length}} посещений</h2>`}}).catch(()=>{{document.querySelector('#visits').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить посещения")}});</script></body></html>''')


@web_router.get("/staff/students/{student_id}/payments", response_class=HTMLResponse)
async def web_student_payments_page(student_id: int, context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра платежей"})
    return HTMLResponse(f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Payments · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Staff / Payments</span><h1>Payments,<br>in detail.</h1></section><section class="web-card" id="payments">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Staff web / Payments");SpeedyCRMWeb.json("/api/v1/staff/students/{student_id}/payments").then(data=>{{document.querySelector('#payments').innerHTML=`<h2>${{data.payments.length}} платежей</h2>`}}).catch(()=>{{document.querySelector('#payments').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить платежи")}});</script></body></html>''')


@web_router.get("/staff/students/{student_id}/discounts", response_class=HTMLResponse)
async def web_student_discounts_page(student_id: int, context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра скидок"})
    return HTMLResponse(f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Discounts · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Staff / Discounts</span><h1>Discounts,<br>in scope.</h1></section><section class="web-card" id="discounts">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Staff web / Discounts");SpeedyCRMWeb.json("/api/v1/staff/students/{student_id}/discounts").then(data=>{{document.querySelector('#discounts').innerHTML=`<h2>${{data.discounts.length}} скидок</h2>`}}).catch(()=>{{document.querySelector('#discounts').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить скидки")}});</script></body></html>''')
