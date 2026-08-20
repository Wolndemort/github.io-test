import os
from datetime import date, timedelta
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import AuditEntry, CartOrder, CashEntry, Club, ClubProduct, Discount, DiscountAssignment, PaymentOrder, Student, StudentParent, VisitLog, User, get_session
from services.input_normalization import normalize_ru_phone
from services.audit import audit_event
from services.schedule_utils import normalize_schedule_block
from services.analytics import (
    build_expiry_series,
    build_revenue_series,
    build_visit_series,
    calculate_projected_renewal_revenue,
    reporting_periods,
    calculate_revenue_periods,
)
from services.analytics import calculate_admin_dashboard
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


def _date_range(value: str | None, fallback: date) -> tuple[date, date]:
    start = date.fromisoformat(value) if value else fallback
    return start, start + timedelta(days=60)


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
    return {"club_id": club.id, "forecast": {"students": rows, "discipline_counts": forecast.get("discipline_counts", {}), "expiry_series": expiry["series"], "revenue_series": revenue["series"], "visit_series": visits_chart["series"], "peak_expiry_count": expiry["peak_count"], "peak_revenue_amount": revenue["peak_amount"], "peak_visit_count": visits_chart["peak_count"]}, "ranges": {"expiry": [start.isoformat(), finish.isoformat()], "revenue": [revenue_start.isoformat(), revenue_finish.isoformat()], "visits": [visits_start.isoformat(), visits_finish.isoformat()]}, "read_only": True}


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


@revenue_router.get("/data")
async def revenue_data(
    request: Request,
    context: AuthContext | None = Depends(web_context),
    session: AsyncSession = Depends(get_session),
):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра выручки"})
    payments = list((await session.execute(select(PaymentOrder).where(PaymentOrder.club_id == actor.club_id, PaymentOrder.status == "CONFIRMED"))).scalars().all())
    cart_orders = list((await session.execute(select(CartOrder).where(CartOrder.club_id == actor.club_id, CartOrder.status == "CONFIRMED"))).scalars().all())
    return {"club_id": actor.club_id, "totals": calculate_revenue_periods(payments + cart_orders), "read_only": True}


@students_router.get("/data")
async def students_data(
    request: Request,
    context: AuthContext | None = Depends(web_context),
    session: AsyncSession = Depends(get_session),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра клиентов"})
    query = select(Student).where(Student.club_id == actor.club_id)
    if q and q.strip():
        query = query.where(Student.name.ilike(f"%{q.strip()}%"))
    students = list((await session.execute(query.order_by(Student.name).offset(offset).limit(limit))).scalars().all())
    return {"club_id": actor.club_id, "students": [{"id": student.id, "name": student.name, "discipline": student.discipline, "parent_id": student.parent_id} for student in students], "pagination": {"limit": limit, "offset": offset, "returned": len(students), "query": q or ""}, "read_only": True}


@students_router.get("/{student_id}")
async def student_detail_data(student_id: int, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра клиентов"})
    student = await session.scalar(select(Student).where(Student.id == student_id, Student.club_id == actor.club_id))
    if not student:
        raise HTTPException(status_code=404, detail={"code": "student_not_found", "message": "Клиент не найден"})
    return {"club_id": actor.club_id, "student": {"id": student.id, "name": student.name, "discipline": student.discipline, "expire_date": student.expire_date.isoformat() if student.expire_date else None, "balance_lessons": student.balance_lessons or 0, "is_frozen": bool(student.is_frozen)}, "read_only": True}


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
):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра сводки"})
    students = list((await session.execute(select(Student).where(Student.club_id == actor.club_id))).scalars().all())
    visits = list((await session.execute(select(VisitLog).where(VisitLog.club_id == actor.club_id))).scalars().all())
    return {"club_id": actor.club_id, "metrics": calculate_admin_dashboard(students, visit_logs=visits), "read_only": True}


@cash_router.get("/data")
async def cash_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "cash_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра кассы"})
    entries = list((await session.execute(select(CashEntry).where(CashEntry.club_id == actor.club_id))).scalars().all())
    income = sum(int(getattr(entry, "amount_kopecks", 0) or 0) for entry in entries if entry.entry_type == "income")
    expense = sum(int(getattr(entry, "amount_kopecks", 0) or 0) for entry in entries if entry.entry_type == "expense")
    return {"club_id": actor.club_id, "income_kopecks": income, "expense_kopecks": expense, "balance_kopecks": income - expense, "read_only": True}


@sales_router.get("/data")
async def sales_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра продаж"})
    payments = list((await session.execute(select(PaymentOrder).where(PaymentOrder.club_id == actor.club_id, PaymentOrder.status == "CONFIRMED"))).scalars().all())
    orders = list((await session.execute(select(CartOrder).where(CartOrder.club_id == actor.club_id, CartOrder.status == "CONFIRMED"))).scalars().all())
    return {"club_id": actor.club_id, "sales_count": len(payments) + len(orders), "amount_kopecks": sum(int(getattr(row, "amount_kopecks", 0) or 0) for row in payments + orders), "read_only": True}


@sales_router.get("/{order_id}")
async def sale_detail_data(order_id: str, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions: raise HTTPException(status_code=403, detail={"code": "permission_denied"})
    payment = await session.scalar(select(PaymentOrder).where(PaymentOrder.id == order_id, PaymentOrder.club_id == actor.club_id, PaymentOrder.status == "CONFIRMED"))
    if not payment: raise HTTPException(status_code=404, detail={"code": "sale_not_found"})
    return {"club_id": actor.club_id, "sale": {"id": payment.id, "student_id": payment.student_id, "amount_kopecks": payment.amount_kopecks, "created_at": payment.created_at.isoformat() if payment.created_at else None, "type": payment.type}, "read_only": True}


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
    products = list((await session.execute(select(ClubProduct).where(ClubProduct.club_id == actor.club_id, ClubProduct.is_active.is_(True)))).scalars().all())
    return {"club_id": actor.club_id, "products": [{"id": p.id, "name": p.name, "category": p.category, "price_kopecks": p.price_kopecks, "stock": p.stock} for p in products], "read_only": True}


@catalog_router.get("/products/{product_id}")
async def product_detail_data(product_id: int, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "products_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра товара"})
    product = await session.scalar(select(ClubProduct).where(ClubProduct.id == product_id, ClubProduct.club_id == actor.club_id, ClubProduct.is_active.is_(True)))
    if not product:
        raise HTTPException(status_code=404, detail={"code": "product_not_found", "message": "Товар не найден"})
    return {"club_id": actor.club_id, "product": {"id": product.id, "name": product.name, "category": product.category, "price_kopecks": product.price_kopecks, "stock": product.stock, "details": product.details}, "read_only": True}


@catalog_router.get("/discounts")
async def discounts_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра скидок"})
    discounts = list((await session.execute(select(Discount).where(Discount.club_id == actor.club_id, Discount.is_active.is_(True)).order_by(Discount.priority, Discount.name))).scalars().all())
    return {"club_id": actor.club_id, "discounts": [{"id": d.id, "name": d.name, "kind": d.kind, "value": d.value, "scope": d.scope} for d in discounts], "read_only": True}


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


@settings_router.get("/legal")
async def legal_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions: raise HTTPException(status_code=403, detail={"code":"permission_denied"})
    club = await session.scalar(select(Club).where(Club.id == actor.club_id)); settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}
    legal = settings.get("legal", {}) if isinstance(settings.get("legal", {}), dict) else {}
    return {"club_id": actor.club_id, "legal": {key: legal.get(key) for key in ("provider_name","provider_type","inn","ogrn","legal_address","club_address","email","phone","document_version")}, "read_only": True}

@settings_router.get("/camera")
async def camera_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "qr_checkin" not in actor.permissions: raise HTTPException(status_code=403, detail={"code":"permission_denied"})
    club = await session.scalar(select(Club).where(Club.id == actor.club_id)); settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}
    camera = settings.get("camera", {}) if isinstance(settings.get("camera", {}), dict) else {}
    return {"club_id": actor.club_id, "camera": {"enabled": bool(camera.get("enabled", False)), "name": str(camera.get("name", "camera1"))[:100]}, "read_only": True}

@settings_router.get("/features")
async def features_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context); club = await session.scalar(select(Club).where(Club.id == actor.club_id)); settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}
    features = settings.get("features", {}) if isinstance(settings.get("features", {}), dict) else {}
    return {"club_id": actor.club_id, "features": {str(k): bool(v) for k, v in features.items()}, "read_only": True}


@settings_router.get("/limits")
async def limits_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    club = await session.scalar(select(Club).where(Club.id == actor.club_id))
    settings = club.club_settings if club and isinstance(club.club_settings, dict) else {}
    limits = settings.get("limits", {}) if isinstance(settings.get("limits", {}), dict) else {}
    safe_keys = ("session_timeout_minutes", "freeze_price_per_day", "max_upload_mb", "max_students")
    safe = {key: limits.get(key) for key in safe_keys if key in limits}
    return {"club_id": actor.club_id, "limits": safe, "read_only": True}


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


@checkin_router.get("/data")
async def checkin_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session), limit: int = Query(default=50, ge=1, le=100)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "qr_checkin" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра проходов"})
    visits = list((await session.execute(select(VisitLog).where(VisitLog.club_id == actor.club_id).order_by(VisitLog.visited_at.desc()).limit(limit))).scalars().all())
    return {"club_id": actor.club_id, "visits": [{"student_id": v.student_id, "visited_at": v.visited_at.isoformat() if v.visited_at else None, "source": v.source} for v in visits], "read_only": True}


@freeze_router.get("/data")
async def staff_freeze_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра заморозок"})
    students = list((await session.execute(select(Student).where(Student.club_id == actor.club_id, Student.is_frozen == 1).order_by(Student.name))).scalars().all())
    return {"club_id": actor.club_id, "frozen": [{"id": s.id, "name": s.name, "discipline": s.discipline, "frozen_at": s.frozen_at.isoformat() if s.frozen_at else None, "frozen_days": s.frozen_days} for s in students], "read_only": True}


@client_router.get("/cabinet/data")
async def client_cabinet_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    students = list((await session.execute(select(Student).where(Student.club_id == actor.club_id, Student.parent_id == actor.user_id).order_by(Student.name))).scalars().all())
    return {"club_id": actor.club_id, "user_id": actor.user_id, "students": [{"id": s.id, "name": s.name, "discipline": s.discipline, "expire_date": s.expire_date.isoformat() if s.expire_date else None, "balance_lessons": s.balance_lessons or 0} for s in students], "read_only": True}


@client_router.get("/me")
async def client_me(request: Request, context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    return {"user_id": actor.user_id, "club_id": actor.club_id, "actor_type": actor.actor_type, "auth_source": actor.auth_source, "read_only": True}


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
    return {"club_id": actor.club_id, "products": [{"id": p.id, "name": p.name, "category": p.category, "price_kopecks": p.price_kopecks, "details": p.details} for p in products], "read_only": True}


@client_router.get("/discounts/data")
async def client_discounts_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    discounts = list((await session.execute(select(Discount).join(DiscountAssignment, DiscountAssignment.discount_id == Discount.id).where(DiscountAssignment.user_id == actor.user_id, DiscountAssignment.club_id == actor.club_id, Discount.club_id == actor.club_id, Discount.is_active.is_(True)))).scalars().all())
    return {"club_id": actor.club_id, "discounts": [{"id": d.id, "name": d.name, "kind": d.kind, "value": d.value, "scope": d.scope} for d in discounts], "read_only": True}


@client_router.get("/tariffs/data")
async def client_tariffs_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor=require_web_context(context); club=await session.scalar(select(Club).where(Club.id==actor.club_id)); settings=club.club_settings if club and isinstance(club.club_settings,dict) else {}; disciplines=settings.get("disciplines",{}) if isinstance(settings.get("disciplines",{}),dict) else {}
    return {"club_id":actor.club_id,"tariffs":{name:block.get("tariffs",[]) for name,block in disciplines.items() if isinstance(block,dict)},"read_only":True}

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
    actor=require_web_context(context); students=list((await session.execute(select(Student.id).where(Student.club_id==actor.club_id,Student.parent_id==actor.user_id))).scalars().all()); visits=[]
    if students: visits=list((await session.execute(select(VisitLog).where(VisitLog.club_id==actor.club_id,VisitLog.student_id.in_(students)))).scalars().all())
    return {"club_id":actor.club_id,"student_count":len(students),"visit_count":len(visits),"read_only":True}

@client_router.get("/summary/subscriptions")
async def client_subscription_summary(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor=require_web_context(context); students=list((await session.execute(select(Student).where(Student.club_id==actor.club_id,Student.parent_id==actor.user_id))).scalars().all()); active=sum(1 for s in students if s.expire_date and s.balance_lessons and not s.is_frozen)
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
    student_ids = list((await session.execute(select(Student.id).where(Student.club_id == actor.club_id, Student.parent_id == actor.user_id))).scalars().all())
    visits = []
    if student_ids:
        visits = list((await session.execute(select(VisitLog).where(VisitLog.club_id == actor.club_id, VisitLog.student_id.in_(student_ids)).order_by(VisitLog.visited_at.desc()).limit(limit))).scalars().all())
    return {"club_id": actor.club_id, "history": [{"student_id": v.student_id, "visited_at": v.visited_at.isoformat() if v.visited_at else None, "source": v.source} for v in visits], "read_only": True}


@client_router.get("/freeze/data")
async def client_freeze_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    students = list((await session.execute(select(Student).where(Student.club_id == actor.club_id, Student.parent_id == actor.user_id))).scalars().all())
    return {"club_id": actor.club_id, "students": [{"id": s.id, "name": s.name, "is_frozen": bool(s.is_frozen), "frozen_until": s.frozen_until.isoformat() if getattr(s, "frozen_until", None) else None} for s in students], "read_only": True}


@client_router.get("/subscriptions/data")
async def client_subscriptions_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    students = list((await session.execute(select(Student).where(Student.club_id == actor.club_id, Student.parent_id == actor.user_id).order_by(Student.name))).scalars().all())
    return {"club_id": actor.club_id, "subscriptions": [{"student_id": s.id, "student_name": s.name, "discipline": s.discipline, "expire_date": s.expire_date.isoformat() if s.expire_date else None, "balance_lessons": s.balance_lessons or 0, "is_active": bool(s.expire_date and s.balance_lessons and not s.is_frozen)} for s in students], "read_only": True}


@client_router.get("/purchases/data")
async def client_purchases_data(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session), limit: int = Query(default=50, ge=1, le=100)):
    actor = require_web_context(context)
    orders = list((await session.execute(select(CartOrder).where(CartOrder.club_id == actor.club_id, CartOrder.user_id == actor.user_id, CartOrder.status == "CONFIRMED").order_by(CartOrder.created_at.desc()).limit(limit))).scalars().all())
    return {"club_id": actor.club_id, "purchases": [{"order_id": o.id, "amount_kopecks": o.amount_kopecks, "created_at": o.created_at.isoformat() if o.created_at else None, "discount_name": o.discount_name} for o in orders], "read_only": True}


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
    if actor.actor_type == "staff" and "forecast_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра прогноза"})
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Forecast · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Analytics / Forecast</span><h1>Future,<br>measured.</h1><p>Прогноз продлений, посещений и выручки на основе данных клуба.</p></section><section class="web-grid" id="forecast"><article class="web-card"><h2>Renewals</h2><div class="web-value" data-value="students">—</div><div class="web-status">клиентов в прогнозе</div></article><article class="web-card"><h2>Revenue</h2><div class="web-value" data-value="revenue">—</div><div class="web-status">пиковый день</div></article><article class="web-card"><h2>Visits</h2><div class="web-value" data-value="visits">—</div><div class="web-status">пиковая посещаемость</div></article></section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation();SpeedyCRMWeb.json("/api/v1/staff/forecast/data").then(data=>{document.querySelector('[data-value="students"]').textContent=data.forecast.students.length;document.querySelector('[data-value="revenue"]').textContent=data.forecast.peak_revenue_amount ?? 0;document.querySelector('[data-value="visits"]').textContent=data.forecast.peak_visit_count ?? 0}).catch(()=>{document.querySelector('#forecast').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить данные прогноза")});</script></body></html>''')


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
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Students · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Staff / Students</span><h1>People,<br>in focus.</h1><p>Клиенты текущего клуба в едином read-only списке.</p></section><section class="web-card" id="students"><form id="student-filter"><input name="q" placeholder="Поиск по имени"><button type="submit">Найти</button></form><div data-student-results>Загрузка списка…</div></section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Staff web / Students");const results=document.querySelector('[data-student-results]');const load=(q="")=>SpeedyCRMWeb.json(`/api/v1/staff/students/data?limit=50&q=${encodeURIComponent(q)}`).then(data=>{results.innerHTML=`<h2>${data.pagination.returned} клиентов</h2><ul>${data.students.map(student=>`<li>${student.name} · ${student.discipline ?? "—"}</li>`).join("")}</ul>`}).catch(()=>{results.innerHTML=SpeedyCRMWeb.error("Не удалось загрузить клиентов")});document.querySelector("#student-filter").addEventListener("submit",event=>{event.preventDefault();load(new FormData(event.target).get("q"))});load();</script></body></html>''')


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
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Cash · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Staff / Cash</span><h1>Cash,<br>in control.</h1><p>Кассовые показатели клуба в read-only режиме.</p></section><section class="web-grid" id="cash">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Staff web / Cash");SpeedyCRMWeb.json("/api/v1/staff/cash/data").then(data=>{document.querySelector('#cash').innerHTML=`<article class="web-card"><h2>Income</h2><div class="web-value">${data.income_kopecks/100}</div></article><article class="web-card"><h2>Expense</h2><div class="web-value">${data.expense_kopecks/100}</div></article><article class="web-card"><h2>Balance</h2><div class="web-value">${data.balance_kopecks/100}</div></article>`}).catch(()=>{document.querySelector('#cash').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить кассу")});</script></body></html>''')


@web_router.get("/staff/sales", response_class=HTMLResponse)
async def web_sales_page(context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "analytics_view" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра продаж"})
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Sales · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Staff / Sales</span><h1>Sales,<br>clearly.</h1><p>Подтверждённые продажи клуба в read-only режиме.</p></section><section class="web-grid" id="sales">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Staff web / Sales");SpeedyCRMWeb.json("/api/v1/staff/sales/data").then(data=>{document.querySelector('#sales').innerHTML=`<article class="web-card"><h2>Orders</h2><div class="web-value">${data.sales_count}</div></article><article class="web-card"><h2>Total</h2><div class="web-value">${data.amount_kopecks/100}</div></article>`}).catch(()=>{document.querySelector('#sales').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить продажи")});</script></body></html>''')


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
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Schedule · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Staff / Schedule</span><h1>Time,<br>organized.</h1><p>Расписание дисциплин текущего клуба в read-only режиме.</p></section><section class="web-card" id="schedule">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Staff web / Schedule");SpeedyCRMWeb.json("/api/v1/staff/schedule/data").then(data=>{document.querySelector('#schedule').innerHTML=`<h2>${Object.keys(data.schedule).length} дисциплин</h2><ul>${Object.keys(data.schedule).map(name=>`<li>${name}</li>`).join("")}</ul>`}).catch(()=>{document.querySelector('#schedule').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить расписание")});</script></body></html>''')


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


@web_router.get("/client/cabinet", response_class=HTMLResponse)
async def web_client_cabinet_page(context: AuthContext | None = Depends(web_context)):
    require_web_context(context)
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Cabinet · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Client / Cabinet</span><h1>Your club,<br>closer.</h1><p>Ваши абонементы и занятия в read-only режиме.</p></section><section class="web-card" id="cabinet">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Client web / Cabinet");SpeedyCRMWeb.json("/api/v1/client/cabinet/data").then(data=>{document.querySelector('#cabinet').innerHTML=`<h2>${data.students.length} абонементов</h2><ul>${data.students.map(s=>`<li>${s.name} · ${s.discipline ?? "—"} · ${s.balance_lessons} занятий</li>`).join("")}</ul>`}).catch(()=>{document.querySelector('#cabinet').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить кабинет")});</script></body></html>''')


async def _client_page(context, title, endpoint, label):
    require_web_context(context)
    return HTMLResponse(f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{title} · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Client / {label}</span><h1>{title},<br>at hand.</h1><p>Ваши данные в read-only режиме.</p></section><section class="web-card" id="client-data">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Client web / {label}");SpeedyCRMWeb.json("{endpoint}").then(data=>{{const items=data.history||data.students||[];document.querySelector('#client-data').innerHTML=`<h2>${{items.length}} записей</h2>`}}).catch(()=>{{document.querySelector('#client-data').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить данные")}});</script></body></html>''')


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


async def _settings_page(context, title, endpoint, permission=None):
    actor = require_web_context(context)
    if permission and actor.actor_type == "staff" and permission not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра настроек"})
    return HTMLResponse(f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{title} · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Staff / Settings</span><h1>{title},<br>read-only.</h1><p>Безопасное представление настроек клуба.</p></section><section class="web-card" id="settings">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Staff web / {title}");SpeedyCRMWeb.json("{endpoint}").then(data=>{{document.querySelector('#settings').innerHTML=`<h2>Club ${{data.club_id}}</h2><p>Данные загружены безопасно.</p>`}}).catch(()=>{{document.querySelector('#settings').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить настройки")}});</script></body></html>''')


@web_router.get("/staff/settings/legal", response_class=HTMLResponse)
async def web_legal_page(context: AuthContext | None = Depends(web_context)):
    return await _settings_page(context, "Legal", "/api/v1/staff/settings/legal", "analytics_view")


@web_router.get("/staff/settings/camera", response_class=HTMLResponse)
async def web_camera_page(context: AuthContext | None = Depends(web_context)):
    return await _settings_page(context, "Camera", "/api/v1/staff/settings/camera", "qr_checkin")


@web_router.get("/staff/settings/features", response_class=HTMLResponse)
async def web_features_page(context: AuthContext | None = Depends(web_context)):
    return await _settings_page(context, "Features", "/api/v1/staff/settings/features")


@web_router.get("/staff/settings/limits", response_class=HTMLResponse)
async def web_limits_page(context: AuthContext | None = Depends(web_context)):
    require_web_context(context)
    return await _settings_page(context, "Limits", "/api/v1/staff/settings/limits")


@web_router.get("/staff/settings/branding", response_class=HTMLResponse)
async def web_branding_page(context: AuthContext | None = Depends(web_context)):
    require_web_context(context)
    return await _settings_page(context, "Branding", "/api/v1/staff/settings/branding")


@web_router.get("/staff/settings/integrations", response_class=HTMLResponse)
async def web_integrations_page(context: AuthContext | None = Depends(web_context)):
    require_web_context(context)
    return await _settings_page(context, "Integrations", "/api/v1/staff/settings/integrations")


@web_router.get("/staff/checkin", response_class=HTMLResponse)
async def web_checkin_page(context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    if actor.actor_type == "staff" and "qr_checkin" not in actor.permissions:
        raise HTTPException(status_code=403, detail={"code": "permission_denied", "message": "Нет права просмотра проходов"})
    return HTMLResponse('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Check-in · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><div class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Staff / Check-in</span><h1>Visits,<br>visible.</h1><p>Последние проходы клуба без возможности изменения данных.</p></section><section class="web-card" id="checkin">Загрузка…</section></div></div><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Staff web / Check-in");SpeedyCRMWeb.json("/api/v1/staff/checkin/data").then(data=>{document.querySelector('#checkin').innerHTML=`<h2>${data.visits.length} проходов</h2>`}).catch(()=>{document.querySelector('#checkin').innerHTML=SpeedyCRMWeb.error("Не удалось загрузить проходы")});</script></body></html>''')


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
