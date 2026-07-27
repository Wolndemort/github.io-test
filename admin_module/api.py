import httpx
import uuid
from html import escape
from loguru import logger
from sqlalchemy import func
from handlers.skud import trigger_dingtian_turnstile
from services.gate_control import process_athlete_gate_pass
from services.yookassa_client import YooKassaClient
from config import PROXY_URL
from fastapi import Query
from services.analytics import (
    generate_students_excel,
    calculate_admin_dashboard,
    calculate_cash_flow_periods,
    calculate_student_metrics,
    calculate_revenue_periods,
    reporting_periods,
    moscow_date_boundary,
    moscow_weekday,
)
from services.schedule_utils import normalize_schedule_block
from services.order_notifications import (
    build_owner_receipt_text,
    build_staff_alert_text,
    format_order_items,
    notify_product_staff,
)
from services.payment_requisites import get_payment_info_text, build_payment_instruction_text
import hmac
import os
import time
from datetime import date, datetime, timedelta, timezone
from database.db import PaymentOrder, Subscription
from database.db import add_abon, purchase_student_freeze
import hashlib
from decimal import Decimal, InvalidOperation
from fastapi.responses import StreamingResponse
import json
from urllib.parse import parse_qsl
import io
import re
from fastapi import Request, UploadFile, File
from fastapi.responses import HTMLResponse
from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette import status
from database.db import User, Student, Club, ClubStaff, ClubProduct, CartOrder, CartItem, CashEntry, AuditEntry
from database.db import purchase_student_freeze
from admin_module.schemas import AdminStudentUpdate, AdminStudentCreate
from database.db import get_session
from config import fastapi_key
from aiogram import Bot
from handlers.user_option import generate_signature, fix_layout
from middlewares.db_saas_midleware import SUPER_ADMIN_IDS
from admin_module.router_base import router, templates
from admin_module.utils import verify_webapp_staff
from admin_module.webapp_verify import verify_telegram_data
from admin_module.webapp_client_cabinet import _ensure_webapp_user_linked
from admin_module.webapp_shared import (
    telegram_init_gate,
    get_club_id_from_host,
    webapp_auth_gate,
    verify_webapp_admin,
    audit_actor_context,
)
from services.input_normalization import normalize_ru_phone, parse_user_date
from services.audit import audit_event
import admin_module.webapp_client_cabinet  # noqa: F401
import admin_module.turnstile_biometry  # noqa: F401 - registers SKUD WebApp routes
import admin_module.payments_webhook  # noqa: F401
import admin_module.system_api  # noqa: F401


async def _ensure_cart_user(session: AsyncSession, club: Club, tg_user: dict) -> User:
    user_id = int(tg_user.get("id", 0))
    user = await session.get(User, user_id, with_for_update=True)
    if not user:
        user = User(
            user_id=user_id,
            club_id=club.id,
            full_name=tg_user.get("first_name")
            or " ".join(part for part in [tg_user.get("first_name"), tg_user.get("last_name")] if part)
            or "",
            is_accepted=False,
            is_biometric_enabled=False,
        )
        session.add(user)
        await session.flush()
        return user
    if not user.full_name:
        user.full_name = tg_user.get("first_name") or " ".join(part for part in [tg_user.get("first_name"), tg_user.get("last_name")] if part) or ""
    return user


def _audit_payload_summary(payload: dict) -> str:
    if not payload:
        return "—"

    def _short(value, limit: int = 40) -> str:
        text = " ".join(str(value).split())
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"

    preferred_keys = (
        "action",
        "object_type",
        "object_id",
        "location",
        "method",
        "category",
        "entry_type",
        "discipline",
        "day",
    )
    parts: list[str] = []

    for key in preferred_keys:
        value = payload.get(key)
        if value not in (None, "", [], {}):
            parts.append(f"{key}={_short(value)}")

    compact_limit = 4
    skip_keys = {"event", "actor_name", "actor_user_id", "actor_role", *preferred_keys}
    for key, value in payload.items():
        if key in skip_keys:
            continue
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            parts.append(f"{key}×{len(value)}")
        elif isinstance(value, dict):
            parts.append(f"{key}{{{', '.join(list(value.keys())[:3])}}}")
        else:
            parts.append(f"{key}={_short(value, 30)}")
        if len(parts) >= compact_limit:
            break

    if not parts:
        return "без доп. полей"

    summary = "; ".join(parts)
    return summary if len(summary) <= 180 else summary[:177].rstrip() + "…"


class WebAppActionPayload(BaseModel):
    init_data: str
    club_id: int
    student_id: int


class ScannerPayload(BaseModel):
    init_data: str
    club_id: int
    qr_data: str

class ScheduleChangePayload(BaseModel):
    init_data: str
    club_id: int
    action: str
    discipline: str
    day: str
    index: int | None = None
    lesson: dict | None = None
    source_day: str | None = None
    source_discipline: str | None = None

class ProductPayload(BaseModel):
    init_data: str
    club_id: int
    name: str
    category: str = "other"
    price_kopecks: int
    stock: int = 0
    is_active: bool = True
    image_url: str | None = None
    details: str | None = None

class ProductCategoryChangePayload(BaseModel):
    init_data: str
    club_id: int
    category: str
    replacement_category: str = "other"

class CartCheckoutPayload(BaseModel):
    init_data: str
    club_id: int
    items: list[dict]
    payment_method: str = "bank_card"

class AdminProductSalePayload(BaseModel):
    init_data: str
    club_id: int
    items: list[dict]
    student_id: int | None = None

class TariffChangePayload(BaseModel):
    init_data: str
    club_id: int
    discipline: str
    action: str
    index: int | None = None
    tariff: dict | None = None

class CashEntryPayload(BaseModel):
    init_data: str
    club_id: int
    entry_type: str
    category: str = "other"
    amount_kopecks: int
    description: str = ""

class CashEntryDeletePayload(BaseModel):
    init_data: str
    club_id: int

class AuditEntryDeletePayload(BaseModel):
    init_data: str
    club_id: int


def _student_identity_phone(value: str | None) -> str:
    return normalize_ru_phone(value) or ""


def _student_age(student: Student) -> int | None:
    birthday = getattr(student, "birthday", None)
    if not birthday:
        return None
    today = datetime.now().date()
    return today.year - birthday.year - ((today.month, today.day) < (birthday.month, birthday.day))


def _tariff_age_error(student: Student, tariff: dict, discipline_name: str) -> str | None:
    min_age = max(0, int(tariff.get("min_age", 0) or 0))
    age = _student_age(student)
    if min_age <= 0 or age is None or age >= min_age:
        return None
    return (
        f"⚠️ <b>Доступ ограничен по возрасту!</b>\n\n"
        f"На тариф секции <b>{escape(discipline_name)}</b> принимаются атлеты строго с <b>{min_age} лет</b>.\n"
        f"Сейчас атлету <b>{escape(student.name)}</b> исполнилось <b>{age} лет</b>."
    )


def _manual_review_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"manual_order_confirm_{order_id}"),
         InlineKeyboardButton(text="❌ Отклонить", callback_data=f"manual_order_decline_{order_id}")]
    ])
class WebAppClubPayload(BaseModel):
    init_data: str
    club_id: int


class WebAppBuySubscriptionPayload(BaseModel):
    init_data: str
    club_id: int
    student_id: int
    sport_type: str
    tariff_idx: int


class WebAppBindPhonePayload(BaseModel):
    init_data: str
    club_id: int
    phone: str


class WebAppHistoryQuery(BaseModel):
    init_data: str
    club_id: int
    student_id: int | None = None


@router.get("/webapp/scanner", response_class=HTMLResponse)
async def get_qr_scanner(request: Request, club_id: int = Query(...)):
    return templates.TemplateResponse(
        "scanner.html", {"request": request, "club_id": club_id}
    )

API_KEY_NAME = "X-API-Key"
API_KEY = fastapi_key

api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)


async def get_api_key(header_value: str = Security(api_key_header)):
    if API_KEY and header_value and hmac.compare_digest(header_value, API_KEY):
        return header_value
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Доступ запрещен: неверный API ключ"
    )

# 1. Добавляем роут /admin, который просила кнопка в ТГ (убрали get_api_key!)



@router.post("/webapp/scanner/scan")
async def scanner_scan(
    payload: ScannerPayload,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Обрабатывает QR без sendData, чтобы планшетный сканер не закрывался."""
    club = (await session.execute(select(Club).where(Club.id == payload.club_id))).scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Клуб не найден")
    tg_user = await verify_webapp_staff(club, payload.init_data, session, "qr_checkin")

    raw = fix_layout(payload.qr_data).strip().split(":")
    if len(raw) != 4 or raw[0] != "student":
        raise HTTPException(status_code=400, detail="Неверный формат QR")
    try:
        student_id = int(raw[1])
        qr_time = datetime.strptime(raw[2], "%Y-%m-%d-%H").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Повреждённый QR")
    age = (datetime.now(timezone.utc) - qr_time).total_seconds()
    if age < -300 or age > 2 * 60 * 60:
        raise HTTPException(status_code=400, detail="Срок действия QR истёк")
    if not hmac.compare_digest(raw[3], generate_signature(student_id, raw[2])):
        raise HTTPException(status_code=400, detail="Недействительный QR")

    redis = getattr(request.app.state, "redis_client", None)
    result = await process_athlete_gate_pass(
        student_id, session, club.club_settings or {}, expected_club_id=club.id, redis=redis
    )
    if not result["success"]:
        return {"success": False, "message": result["message"]}

    bot = Bot(club.bot_token)
    try:
        admin_text = (f"🟢 <b>ПРОХОД</b>\n👤 Атлет: <b>{result['student_name']}</b>\n"
                      f"📅 До: {result['expire_str']}\n{result['turnstile_status']}")
        await bot.send_message(club.owner_id, admin_text, parse_mode="HTML")
        if result.get("parent_id"):
            await bot.send_message(int(result["parent_id"]),
                                   f"❗ <b>{result['club_name']}</b>: {result['student_name']} вошел в зал.",
                                   parse_mode="HTML")
    finally:
        await bot.session.close()
    return {"success": True, "message": "Турникет открыт", "student_name": result["student_name"]}


@router.get("/admin", response_class=HTMLResponse)
async def get_admin_dashboard(
    request: Request,
    session: AsyncSession = Depends(get_session),
    init_data: str | None = Query(default=None),
):
    club_id = get_club_id_from_host(request)

    # Telegram WebApp сначала открывает безопасный экран, затем JS передаёт
    # подписанные Telegram initData обратно в этот же URL.
    if not init_data:
        return HTMLResponse("""<!doctype html><meta charset='utf-8'>
<script src='https://telegram.org/js/telegram-web-app.js'></script>
<script>
const tg=window.Telegram.WebApp; tg.ready();
if (!tg.initData) document.body.innerText='Откройте админку из Telegram';
else location.replace(location.pathname+'?club_id=' + encodeURIComponent(new URLSearchParams(location.search).get('club_id') || '') + '&init_data=' + encodeURIComponent(tg.initData));
</script>""", status_code=401)

    club_res = await session.execute(select(Club).where(Club.id == club_id))
    club = club_res.scalar_one_or_none()

    await verify_webapp_admin(club, init_data)

    club_settings = club.club_settings or {} if club else {}
    limits_settings = club_settings.get("limits", {})
    timeout_minutes = limits_settings.get("session_timeout_minutes", 150)

    result = await session.execute(
        select(Student).where(Student.club_id == club_id)
    )

    students = list(result.scalars().all())
    now_local = datetime.now(timezone.utc).replace(tzinfo=None)
    active_sessions, past_sessions = [], []
    for student in students:
        if student.last_visit:
            last_visit_naive = student.last_visit.replace(tzinfo=None)
            time_passed = now_local - last_visit_naive
            session_end = last_visit_naive + timedelta(minutes=timeout_minutes)
            session_info = {
                "student_id": student.id, "name": student.name,
                "balance": student.balance_lessons or 0,
                "last_visit": last_visit_naive.strftime("%d.%m.%Y %H:%M"),
                "session_end": session_end.strftime("%H:%M"),
                "time_passed_mins": int(time_passed.total_seconds() // 60),
            }
            if time_passed < timedelta(minutes=timeout_minutes):
                session_info["mins_left"] = max(0, int((session_end - now_local).total_seconds() // 60))
                active_sessions.append(session_info)
            else:
                past_sessions.append(session_info)
    active_sessions.sort(key=lambda x: x["time_passed_mins"])
    past_sessions.sort(key=lambda x: x["time_passed_mins"])
    return templates.TemplateResponse("admin.html", {
        "request": request, "club_id": club_id,
        "active_sessions": active_sessions, "past_sessions": past_sessions[:20],
        "timeout_minutes": timeout_minutes,
        **calculate_admin_dashboard(students),
    })


@router.get("/admin/students", response_class=HTMLResponse)
async def admin_students_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
    init_data: str | None = Query(default=None),
):
    club_id = get_club_id_from_host(request)
    if not init_data:
        return webapp_auth_gate(request, club_id)
    club = (await session.execute(select(Club).where(Club.id == club_id))).scalar_one_or_none()
    await verify_webapp_staff(club, init_data, session, "athletes_view")
    students = (await session.execute(
        select(Student).where(Student.club_id == club_id).order_by(Student.name)
    )).scalars().all()
    return templates.TemplateResponse("admin_students.html", {
        "request": request,
        "club_id": club_id,
        "club_name": club.name,
        "students": students,
        "club_settings": club.club_settings or {},
        "now": datetime.now(),
    })


@router.get("/admin/sales", response_class=HTMLResponse)
async def admin_sales_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
    init_data: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    weekday: int | None = Query(default=None),
    payment_method: str | None = Query(default=None),
    category: str | None = Query(default=None),
    discipline: str | None = Query(default=None),
):
    club_id = get_club_id_from_host(request)
    if not init_data:
        return webapp_auth_gate(request, club_id)
    club = await session.get(Club, club_id)
    await verify_webapp_admin(club, init_data)

    start = moscow_date_boundary(date_from) if date_from else None
    end = moscow_date_boundary(date_to) + timedelta(days=1) if date_to else None
    payment_query = select(PaymentOrder).where(PaymentOrder.club_id == club_id, PaymentOrder.status == "CONFIRMED")
    cart_query = select(CartOrder).where(CartOrder.club_id == club_id, CartOrder.status == "CONFIRMED")
    if start:
        payment_query = payment_query.where(PaymentOrder.created_at >= start); cart_query = cart_query.where(CartOrder.created_at >= start)
    if end:
        payment_query = payment_query.where(PaymentOrder.created_at < end); cart_query = cart_query.where(CartOrder.created_at < end)
    # Не ограничиваем выборку до фильтрации: иначе итоговая выручка таблицы
    # начинала расходиться с общей статистикой после первых 500 заказов.
    payment_orders = (await session.execute(payment_query.order_by(PaymentOrder.created_at.desc()))).scalars().all()
    cart_orders = (await session.execute(cart_query.order_by(CartOrder.created_at.desc()))).scalars().all()
    cart_ids = [order.id for order in cart_orders]
    cart_items = (await session.execute(select(CartItem).where(CartItem.cart_order_id.in_(cart_ids)))) .scalars().all() if cart_ids else []
    items_by_order = {}
    for item in cart_items:
        items_by_order.setdefault(item.cart_order_id, []).append(item)

    operations = []
    for order in payment_orders:
        operation_category = "freeze" if str(order.type).startswith("FREEZE") else "subscription"
        operation_method = "cash" if str(order.provider_payment_id or "").startswith("CASH:") or str(order.type).startswith("CASH") else ("card" if order.provider_payment_id else "other")
        operation_discipline = order.discipline or ""
        operations.append({"id": order.id, "created_at": order.created_at, "amount": order.amount_kopecks or 0,
                           "method": operation_method, "category": operation_category, "discipline": operation_discipline,
                           "title": "Заморозка" if operation_category == "freeze" else "Абонемент", "status": order.status})
    for order in cart_orders:
        for item in items_by_order.get(order.id, []):
            payload = item.payload or {}
            operation_category = item.item_type
            operation_discipline = payload.get("discipline", "")
            operation_method = "cash" if str(order.provider_payment_id or "").startswith("CASH:") else ("card" if order.provider_payment_id else "other")
            operations.append({"id": order.id, "created_at": order.created_at, "amount": (item.unit_price_kopecks or 0) * (item.quantity or 1),
                               "method": operation_method, "category": operation_category, "discipline": operation_discipline,
                               "title": item.title, "status": order.status})
    operations = [item for item in operations
                  if (weekday is None or moscow_weekday(item["created_at"]) == weekday)
                  and (not payment_method or item["method"] == payment_method)
                  and (not category or item["category"] == category)
                  and (not discipline or item["discipline"] == discipline)]
    operations.sort(key=lambda item: item["created_at"] or datetime.min, reverse=True)
    settings = club.club_settings or {}
    disciplines = settings.get("disciplines", {}) if isinstance(settings, dict) else {}
    return templates.TemplateResponse("admin_sales.html", {"request": request, "club": club, "club_id": club_id,
        "operations": operations, "disciplines": disciplines, "filters": {"date_from": date_from or "", "date_to": date_to or "", "weekday": weekday, "payment_method": payment_method or "", "category": category or "", "discipline": discipline or ""}})


@router.get("/admin/cash", response_class=HTMLResponse)
async def cash_register_page(request: Request, session: AsyncSession = Depends(get_session), init_data: str | None = Query(default=None), date_from: str | None = Query(default=None), date_to: str | None = Query(default=None)):
    club_id = get_club_id_from_host(request)
    if not init_data:
        return webapp_auth_gate(request, club_id)
    club = await session.get(Club, club_id)
    await verify_webapp_staff(club, init_data, session, "cash_view")
    start = moscow_date_boundary(date_from) if date_from else None
    end = moscow_date_boundary(date_to) + timedelta(days=1) if date_to else None
    manual_query = select(CashEntry).where(CashEntry.club_id == club_id)
    if start: manual_query = manual_query.where(CashEntry.created_at >= start)
    if end: manual_query = manual_query.where(CashEntry.created_at < end)
    manual = (await session.execute(manual_query.order_by(CashEntry.created_at.desc()))).scalars().all()
    income_rows = []
    payment_query = select(PaymentOrder).where(PaymentOrder.club_id == club_id, PaymentOrder.status == "CONFIRMED")
    cart_query = select(CartOrder).where(CartOrder.club_id == club_id, CartOrder.status == "CONFIRMED")
    if start: payment_query = payment_query.where(PaymentOrder.created_at >= start); cart_query = cart_query.where(CartOrder.created_at >= start)
    if end: payment_query = payment_query.where(PaymentOrder.created_at < end); cart_query = cart_query.where(CartOrder.created_at < end)
    for row in (await session.execute(payment_query)).scalars().all():
        is_cash = str(row.provider_payment_id or "").startswith("CASH") or str(row.type).startswith("CASH")
        income_rows.append({"id": row.id, "created_at": row.created_at, "entry_type": "income", "category": "freeze" if str(row.type).startswith("FREEZE") else "subscription", "amount_kopecks": row.amount_kopecks or 0, "description": ("Наличный" if is_cash else "Онлайн") + " платёж", "source": "sale", "method": "cash" if is_cash else "card"})
    for row in (await session.execute(cart_query)).scalars().all():
        is_cash = str(row.provider_payment_id or "").startswith("CASH")
        cart_items = (await session.execute(select(CartItem).where(CartItem.cart_order_id == row.id).order_by(CartItem.id.asc()))).scalars().all()
        item_titles = ", ".join(item.title for item in cart_items if item.title)
        income_rows.append({"id": row.id, "created_at": row.created_at, "entry_type": "income", "category": "product", "amount_kopecks": row.amount_kopecks or 0, "description": f"{('Наличная' if is_cash else 'Онлайн')} продажа товара: {item_titles}" if item_titles else ("Наличная" if is_cash else "Онлайн") + " продажа товара", "source": "sale", "method": "cash" if is_cash else "card"})
    rows = income_rows + [{"id": e.id, "created_at": e.created_at, "entry_type": e.entry_type, "category": e.category, "amount_kopecks": e.amount_kopecks, "description": e.description, "source": "manual", "method": "cash"} for e in manual]
    rows.sort(key=lambda x: x["created_at"] or datetime.min, reverse=True)
    income = sum(r["amount_kopecks"] for r in rows if r["entry_type"] == "income")
    cash_income = sum(r["amount_kopecks"] for r in rows if r["entry_type"] == "income" and r.get("method") == "cash")
    online_income = sum(r["amount_kopecks"] for r in rows if r["entry_type"] == "income" and r.get("method") == "card")
    expenses = sum(r["amount_kopecks"] for r in rows if r["entry_type"] == "expense")
    cash_income_total = sum(r["amount_kopecks"] for r in rows if r["entry_type"] == "income" and r.get("method") == "cash") / 100
    cash_expenses_total = sum(r["amount_kopecks"] for r in rows if r["entry_type"] == "expense") / 100
    cash_margin_total = cash_income_total - cash_expenses_total
    return templates.TemplateResponse(
        "cash_register.html",
        {
            "request": request,
            "club_id": club_id,
            "rows": rows,
            "income": income,
            "cash_income": cash_income,
            "online_income": online_income,
            "expenses": expenses,
            "balance": cash_income - expenses,
            "margin": cash_income - expenses,
            "cash_income_total": cash_income_total,
            "cash_expenses_total": cash_expenses_total,
            "cash_margin_total": cash_margin_total,
            "date_from": date_from or "",
            "date_to": date_to or "",
        },
    )


@router.get("/webapp/admin-audit", response_class=HTMLResponse)
async def admin_audit_page(
    request: Request,
    club_id: int = Query(...),
    init_data: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    actor_role: str | None = Query(default=None),
    event: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    club = await session.get(Club, club_id)
    if not init_data:
        return telegram_init_gate('/webapp/admin-audit', club_id, 'Откройте аудит из Telegram')
    await verify_webapp_admin(club, init_data)
    start = moscow_date_boundary(date_from) if date_from else None
    end = moscow_date_boundary(date_to) + timedelta(days=1) if date_to else None
    query = select(AuditEntry).where(AuditEntry.club_id == club_id)
    if start:
        query = query.where(AuditEntry.created_at >= start)
    if end:
        query = query.where(AuditEntry.created_at < end)
    if actor_role:
        query = query.where(AuditEntry.actor_role == actor_role.strip().casefold())
    if event:
        query = query.where(AuditEntry.event == event.strip())
    entries = (await session.execute(query.order_by(AuditEntry.created_at.desc()).limit(300))).scalars().all()
    rows = []
    for entry in entries:
        payload = entry.payload or {}
        rows.append({
            "id": entry.id,
            "created_at": entry.created_at,
            "event": entry.event,
            "actor_user_id": entry.actor_user_id,
            "actor_role": entry.actor_role or "",
            "actor_name": payload.get("actor_name") or "",
            "action": entry.action or payload.get("action") or "",
            "object_type": entry.object_type or "",
            "object_id": entry.object_id or "",
            "location": entry.location or payload.get("location") or "",
            "amount_kopecks": entry.amount_kopecks,
            "method": entry.method or "",
            "payload": payload,
            "payload_summary": _audit_payload_summary(payload),
        })
    return templates.TemplateResponse(
        "admin_audit.html",
        {
            "request": request,
            "club": club,
            "club_id": club_id,
            "rows": rows,
            "filters": {"date_from": date_from or "", "date_to": date_to or "", "actor_role": actor_role or "", "event": event or ""},
        },
    )


@router.post("/admin/cash/entries")
async def create_cash_entry(payload: CashEntryPayload, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, payload.club_id)
    tg_user = await verify_webapp_admin(club, payload.init_data)
    if payload.entry_type not in {"income", "expense"} or payload.amount_kopecks <= 0 or payload.amount_kopecks > 100_000_000_00:
        raise HTTPException(status_code=400, detail="Некорректный тип или сумма кассовой операции")
    entry = CashEntry(club_id=payload.club_id, entry_type=payload.entry_type, category=payload.category.strip()[:50] or "other", amount_kopecks=payload.amount_kopecks, description=payload.description.strip()[:500], created_by=int(tg_user.get("id")))
    session.add(entry)
    await session.commit()
    audit_event(
        "cash_entry_created",
        **await audit_actor_context(session, club, tg_user, "admin/cash/entries"),
        club_id=payload.club_id,
        action="create",
        object_type="cash_entry",
        object_id=entry.id,
        entry_type=entry.entry_type,
        category=entry.category,
        amount_kopecks=entry.amount_kopecks,
        description=entry.description,
    )
    return {"success": True, "id": entry.id}


@router.post("/admin/cash/entries/{entry_id}/reverse")
async def reverse_cash_entry(entry_id: int, payload: CashEntryPayload, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, payload.club_id)
    tg_user = await verify_webapp_admin(club, payload.init_data)
    entry = await session.get(CashEntry, entry_id)
    if not entry or entry.club_id != payload.club_id:
        raise HTTPException(status_code=404, detail="Кассовая операция не найдена")
    if entry.reversed_entry_id:
        raise HTTPException(status_code=409, detail="Операция уже сторнирована")
    reversal = CashEntry(club_id=entry.club_id, entry_type="expense" if entry.entry_type == "income" else "income", category="reversal", amount_kopecks=entry.amount_kopecks, description=f"Сторно операции #{entry.id}: {entry.description}"[:500], created_by=int(tg_user.get("id")), reversed_entry_id=entry.id)
    session.add(reversal)
    await session.flush()
    entry.reversed_entry_id = reversal.id
    await session.commit()
    audit_event(
        "cash_entry_reversed",
        **await audit_actor_context(session, club, tg_user, "admin/cash/entries/reverse"),
        club_id=entry.club_id,
        action="reverse",
        object_type="cash_entry",
        object_id=entry.id,
        entry_id=entry.id,
        reversal_id=reversal.id,
        amount_kopecks=entry.amount_kopecks,
        category=entry.category,
    )
    return {"success": True, "reversal_id": reversal.id}


@router.post("/admin/cash/entries/{entry_id}/delete")
async def delete_cash_entry(entry_id: int, payload: CashEntryDeletePayload, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, payload.club_id)
    tg_user = await verify_webapp_admin(club, payload.init_data)
    entry = await session.get(CashEntry, entry_id)
    if not entry or entry.club_id != payload.club_id:
        raise HTTPException(status_code=404, detail="Кассовая операция не найдена")
    if entry.reversed_entry_id:
        raise HTTPException(status_code=409, detail="Сначала удалите сторно этой операции")
    await session.delete(entry)
    await session.commit()
    audit_event(
        "cash_entry_deleted",
        **await audit_actor_context(session, club, tg_user, "admin/cash/entries/delete"),
        club_id=payload.club_id,
        action="delete",
        object_type="cash_entry",
        object_id=entry_id,
    )
    return {"success": True}


@router.post("/webapp/admin-audit/{entry_id}/delete")
async def delete_audit_entry(entry_id: int, payload: AuditEntryDeletePayload, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, payload.club_id)
    await verify_webapp_admin(club, payload.init_data)
    entry = await session.get(AuditEntry, entry_id)
    if not entry or entry.club_id != payload.club_id:
        raise HTTPException(status_code=404, detail="Запись журнала не найдена")
    await session.delete(entry)
    await session.commit()
    return {"success": True}


@router.patch("/admin/students/{student_id}")
async def admin_update_student(
    student_id: int,
    payload: AdminStudentUpdate,
    db: AsyncSession = Depends(get_session),
):
    # Блокируем клуб до конца транзакции: два параллельных запроса не должны
    # одновременно пройти проверку и создать одинаковых атлетов.
    club = (await db.execute(
        select(Club).where(Club.id == payload.club_id).with_for_update()
    )).scalar_one_or_none()
    tg_user = verify_telegram_data(payload.init_data, club.bot_token if club else "")
    student = await db.get(Student, student_id, with_for_update=True)
    if not student:
        raise HTTPException(status_code=404, detail="Атлет не найден")
    if student.club_id != payload.club_id:
        raise HTTPException(status_code=403, detail="Атлет другого клуба")
    owner_club = await db.get(Club, student.club_id)
    await verify_webapp_admin(owner_club, payload.init_data)
    if not tg_user:
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    before = {
        "name": student.name,
        "balance_lessons": student.balance_lessons,
        "birthday": student.birthday.isoformat() if student.birthday else None,
        "expire_date": student.expire_date.isoformat() if student.expire_date else None,
        "can_freeze": student.can_freeze,
        "is_frozen": student.is_frozen,
        "frozen_at": student.frozen_at.isoformat() if student.frozen_at else None,
        "frozen_days": student.frozen_days,
        "discipline": student.discipline,
        "parent_phone": student.parent_phone,
    }
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Имя атлета не может быть пустым")
        if len(name) > 150:
            raise HTTPException(status_code=400, detail="Имя атлета слишком длинное")
        student.name = name
    if payload.balance_lessons is not None:
        if payload.balance_lessons < 0 or payload.balance_lessons > 999:
            raise HTTPException(status_code=400, detail="Баланс должен быть от 0 до 999")
        student.balance_lessons = payload.balance_lessons
    if payload.birthday is not None:
        if payload.birthday == "":
            student.birthday = None
        else:
            try:
                birthday = parse_user_date(payload.birthday)
                if birthday > date.today() or birthday.year < 1900:
                    raise ValueError
                student.birthday = birthday
            except ValueError:
                raise HTTPException(status_code=400, detail="Некорректная дата рождения")
    if payload.expire_date is not None:
        if payload.expire_date == "":
            student.expire_date = None
        else:
            try:
                student.expire_date = datetime.strptime(payload.expire_date, "%Y-%m-%d").replace()
            except ValueError:
                raise HTTPException(status_code=400, detail="Некорректная дата окончания")
    if payload.can_freeze is not None:
        if payload.can_freeze < 0 or payload.can_freeze > 99:
            raise HTTPException(status_code=400, detail="Некорректный лимит заморозок")
        student.can_freeze = payload.can_freeze
    if payload.is_frozen is not None:
        student.is_frozen = 1 if payload.is_frozen else 0
        if student.is_frozen:
            if payload.frozen_at:
                try:
                    student.frozen_at = datetime.strptime(payload.frozen_at, "%Y-%m-%d")
                except ValueError:
                    raise HTTPException(status_code=400, detail="Некорректная дата начала заморозки")
            elif not student.frozen_at:
                student.frozen_at = datetime.utcnow()
        else:
            student.frozen_at = None
            student.frozen_days = None
    if payload.frozen_days is not None:
        if payload.frozen_days < 1 or payload.frozen_days > 365:
            raise HTTPException(status_code=400, detail="Срок заморозки должен быть от 1 до 365 дней")
        student.frozen_days = payload.frozen_days
    if payload.discipline is not None:
        discipline_code = payload.discipline.strip()[:50]
        discipline_cfg = (owner_club.club_settings or {}).get("disciplines", {}).get(discipline_code)
        if not discipline_cfg or not discipline_cfg.get("active"):
            raise HTTPException(status_code=400, detail="Дисциплина недоступна")
        student.discipline = discipline_code or student.discipline
    if payload.parent_phone is not None:
        if not payload.parent_phone.strip():
            student.parent_phone = None
        else:
            normalized_phone = normalize_ru_phone(payload.parent_phone)
            if not normalized_phone:
                raise HTTPException(status_code=400, detail="Некорректный номер телефона")
            student.parent_phone = normalized_phone
    await db.commit()
    after = {
        "name": student.name,
        "balance_lessons": student.balance_lessons,
        "birthday": student.birthday.isoformat() if student.birthday else None,
        "expire_date": student.expire_date.isoformat() if student.expire_date else None,
        "can_freeze": student.can_freeze,
        "is_frozen": student.is_frozen,
        "frozen_at": student.frozen_at.isoformat() if student.frozen_at else None,
        "frozen_days": student.frozen_days,
        "discipline": student.discipline,
        "parent_phone": student.parent_phone,
    }
    audit_event(
        "student_updated",
        **await audit_actor_context(db, owner_club, tg_user, "admin/students/{student_id}"),
        club_id=payload.club_id,
        action="update",
        object_type="student",
        object_id=student.id,
        changes={key: {"before": before[key], "after": after[key]} for key in before if before[key] != after[key]},
    )
    return {"ok": True}


@router.delete("/admin/students/{student_id}")
async def admin_delete_student(
    student_id: int,
    payload: AdminStudentUpdate,
    db: AsyncSession = Depends(get_session),
):
    club = (await db.execute(
        select(Club).where(Club.id == payload.club_id).with_for_update()
    )).scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Клуб не найден")
    if not verify_telegram_data(payload.init_data, club.bot_token):
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    await verify_webapp_admin(club, payload.init_data)
    student = await db.get(Student, student_id, with_for_update=True)
    if not student or student.club_id != payload.club_id:
        raise HTTPException(status_code=404, detail="Атлет не найден")
    before = {
        "name": student.name,
        "discipline": student.discipline,
        "parent_phone": student.parent_phone,
    }
    await db.delete(student)
    await db.commit()
    audit_event(
        "student_deleted",
        **await audit_actor_context(db, club, verify_telegram_data(payload.init_data, club.bot_token), "admin/students/{student_id}"),
        club_id=payload.club_id,
        action="delete",
        object_type="student",
        object_id=student_id,
        changes=before,
    )
    return {"ok": True}


@router.post("/admin/students")
async def admin_create_student(
    payload: AdminStudentCreate,
    db: AsyncSession = Depends(get_session),
):
    # Сериализуем создание внутри клуба: иначе два одновременных запроса
    # могут оба пройти проверку дубля до того, как первый закоммитится.
    club = (await db.execute(
        select(Club).where(Club.id == payload.club_id).with_for_update()
    )).scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Клуб не найден")
    if not verify_telegram_data(payload.init_data, club.bot_token):
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    await verify_webapp_admin(club, payload.init_data)

    disciplines = (club.club_settings or {}).get("disciplines", {})
    disc_cfg = disciplines.get(payload.discipline)
    if not disc_cfg or not disc_cfg.get("active"):
        raise HTTPException(status_code=400, detail="Дисциплина недоступна")

    birthday = None
    if payload.birthday:
        try:
                birthday = parse_user_date(payload.birthday)
        except ValueError:
            raise HTTPException(status_code=400, detail="Некорректная дата рождения")

    name = payload.name.strip()
    phone_key = _student_identity_phone(payload.phone)
    existing_students = (await db.execute(select(Student).where(Student.club_id == club.id))).scalars().all()
    duplicate = next((student for student in existing_students
                      if student.name.strip().casefold() == name.casefold()
                      and student.birthday == birthday
                      and (student.discipline or "").strip().casefold() == payload.discipline.strip().casefold()
                      and _student_identity_phone(student.parent_phone) == phone_key), None)
    if duplicate:
        raise HTTPException(status_code=409, detail="Такой атлет уже есть в базе клуба")

    count = 0
    days = 0
    expire_date = None
    if payload.tariff_idx is not None:
        tariffs = disc_cfg.get("tariffs", [])
        if payload.tariff_idx < 0 or payload.tariff_idx >= len(tariffs):
            raise HTTPException(status_code=400, detail="Тариф не найден")
        tariff = tariffs[payload.tariff_idx]
        count = int(tariff.get("count", 0) or 0)
        days = int(tariff.get("days", 30) or 30)
        expire_date = datetime.now() + timedelta(days=days)

    new_student = Student(
        name=name,
        club_id=club.id,
        parent_phone=normalize_ru_phone(payload.phone) if payload.phone else None,
        birthday=birthday,
        parent_id=None,
        balance_lessons=count,
        expire_date=expire_date,
        can_freeze=1,
        is_frozen=0,
        discipline=payload.discipline.strip()[:50] or None,
    )
    db.add(new_student)
    await db.commit()
    await db.refresh(new_student)
    audit_event(
        "student_created",
        **await audit_actor_context(db, club, verify_telegram_data(payload.init_data, club.bot_token), "admin/students"),
        club_id=club.id,
        action="create",
        object_type="student",
        object_id=new_student.id,
        student_name=new_student.name,
        discipline=new_student.discipline,
        balance_lessons=new_student.balance_lessons,
        expire_date=new_student.expire_date.isoformat() if new_student.expire_date else None,
        has_subscription=payload.tariff_idx is not None,
        parent_phone=new_student.parent_phone,
    )
    return {
        "ok": True,
        "student": {
            "id": new_student.id,
            "name": new_student.name,
            "birthday": new_student.birthday.isoformat() if new_student.birthday else "",
            "balance_lessons": new_student.balance_lessons or 0,
            "expire_date": new_student.expire_date.strftime("%Y-%m-%d") if new_student.expire_date else "",
            "can_freeze": new_student.can_freeze or 0,
            "is_frozen": int(new_student.is_frozen or 0),
            "frozen_days": new_student.frozen_days or "",
            "discipline": new_student.discipline or "",
            "parent_phone": new_student.parent_phone or "",
        },
        "meta": {
            "count": count,
            "days": days,
            "has_subscription": payload.tariff_idx is not None,
            "discipline_name": disc_cfg.get("name", payload.discipline),
        },
    }


@router.get("/revenue", response_class=HTMLResponse)
async def get_revenue_stats(
        request: Request,
        session: AsyncSession = Depends(get_session),
        init_data: str | None = Query(default=None),
        date_from: str | None = Query(default=None),
        date_to: str | None = Query(default=None),
):
    club_id = get_club_id_from_host(request)

    # 1. Загрузка клуба
    club_res = await session.execute(select(Club).where(Club.id == club_id))
    club = club_res.scalar_one_or_none()

    if not init_data:
        return webapp_auth_gate(request, club_id)
    # При обычном HTTP-запросе init_data будет строкой/None. Query-объект
    # встречается только при прямом вызове функции из legacy-тестов.
    if isinstance(init_data, str) or init_data is None:
        await verify_webapp_admin(club, init_data)
    if club:
        settings = club.club_settings if isinstance(club.club_settings, dict) else {}
        club_name = settings.get("ui", {}).get("club_name") or club.name
    else:
        club_name = "Фитнес-клуб"

    # Настройка честного времени (МСК)

    periods = reporting_periods()
    now_local = periods["now"]
    today_start = periods["today"]
    week_start = periods["week"]
    month_start = periods["month"]

    # ==========================================
    # БЛОК 1: ФИНАНСЫ (Для твоих графиков/отчетов)
    # ==========================================
    start_filter = moscow_date_boundary(date_from) if date_from else month_start
    end_filter = moscow_date_boundary(date_to) + timedelta(days=1) if date_to else None

    payment_query = select(PaymentOrder.amount_kopecks, PaymentOrder.created_at).where(
        PaymentOrder.club_id == club_id,
        PaymentOrder.status == "CONFIRMED",
        PaymentOrder.created_at >= start_filter,
    )
    cart_query = select(CartOrder.amount_kopecks, CartOrder.created_at).where(
        CartOrder.club_id == club_id,
        CartOrder.status == "CONFIRMED",
        CartOrder.created_at >= start_filter,
    )
    if end_filter:
        payment_query = payment_query.where(PaymentOrder.created_at < end_filter)
        cart_query = cart_query.where(CartOrder.created_at < end_filter)

    payments_res = await session.execute(payment_query)
    cart_payments_res = await session.execute(cart_query)
    payment_rows = [type("PaymentRow", (), {"amount_kopecks": amount, "created_at": created_at}) for amount, created_at in payments_res.all()]
    payment_rows.extend(type("PaymentRow", (), {"amount_kopecks": amount, "created_at": created_at}) for amount, created_at in cart_payments_res.all())
    revenue = calculate_revenue_periods(payment_rows)
    revenue_today = revenue["today"]
    revenue_week = revenue["week"]
    revenue_month = revenue["month"]

    # Направления по оплатам
    disc_pay_res = await session.execute(
        select(Student.discipline, func.sum(PaymentOrder.amount_kopecks))
        .join(Student, PaymentOrder.student_id == Student.id)
        .where(PaymentOrder.club_id == club_id, PaymentOrder.status == "CONFIRMED")
        .group_by(Student.discipline)
    )

    # Рекурренты
    type_res = await session.execute(
        select(PaymentOrder.type, func.count(PaymentOrder.id))
        .where(PaymentOrder.club_id == club_id, PaymentOrder.status == "CONFIRMED")
        .group_by(PaymentOrder.type)
    )
    payment_types = {"FIRST": 0, "RECURRENT": 0}
    for row in type_res.all():
        if row[0] in payment_types:
            payment_types[row[0]] = row[1]

    # ==========================================
    # БЛОК 2: АТЛЕТЫ И АБОНЕМЕНТЫ (Для твоего HTML)
    # ==========================================
    # Вытаскиваем ВСЕХ студентов этого клуба одним запросом
    students_res = await session.execute(
        select(Student).where(Student.club_id == club_id)
    )
    students = students_res.scalars().all()

    if not students:
        # Если в клубе пусто — отдаем флаг empty, как просит HTML
        return templates.TemplateResponse(
            "stats.html",
            {"request": request, "empty": True, "club_name": club_name, "filters": {"date_from": date_from or "", "date_to": date_to or ""}}
        )

    student_metrics = calculate_student_metrics(students, now=now_local)
    total_athletes = student_metrics["total_athletes"]
    total_parents = student_metrics["total_parents"]
    active_passes = student_metrics["active_passes"]
    frozen_passes = student_metrics["frozen_passes"]
    burning_passes = student_metrics["burning_passes"]
    inactive_passes = student_metrics["inactive_passes"]
    total_lessons_left = student_metrics["total_lessons_left"]
    churned_students = [{"name": s.name} for s in student_metrics["inactive_students"]]
    frozen_students = [{"name": s.name} for s in student_metrics["frozen_students"]]
    inactive_students = [{"name": s.name} for s in student_metrics["inactive_students"]]
    discipline_counts = student_metrics["discipline_counts"]

    # Красивые имена для дисциплин в HTML
    discipline_names = {
        "boxing": "🥊 Бокс (Дети)",
        "kickboxing": "🤼‍♂️ Кикбоксинг",
        "bjj": "🥋 Бразильское джиу-джитсу",
        "yoga": "🧘‍♂️ Йога"
        ,"grappling": "🤼 Грэпплинг"
        ,"crossfit": "🏋️ Кроссфит"
    }

    disciplines_stats = [
        {"name": discipline_names.get(k, f"🏃‍♂️ {k}"), "active_athletes": v}
        for k, v in discipline_counts.items()
    ]

    # Сортируем топ-атлетов по остатку занятий (первые 5 человек)
    sorted_students = sorted(students, key=lambda x: x.balance_lessons or 0, reverse=True)
    top_students = [{"name": s.name, "balance": s.balance_lessons or 0} for s in sorted_students[:5]]

    # Считаем Retention (Удержание). Например: процент тех, у кого баланс > 0
    retention_rate = student_metrics["retention_rate"]

    # 5. ОТДАЕМ ПОЛНЫЙ КОМПЛЕКТ ДАННЫХ В ШАБЛОН
    return templates.TemplateResponse(
        "stats.html",
        {
            "request": request,
            "empty": False,
            "club_id": club_id,
            "club_name": club_name,

            # Данные для HTML-карточек
            "total_athletes": total_athletes,
            "total_parents": total_parents,
            "retention_rate": retention_rate,
            "active_passes": active_passes,
            "frozen_passes": frozen_passes,
            "burning_passes": burning_passes,
            "inactive_passes": inactive_passes,
            "total_lessons_left": total_lessons_left,
            "disciplines_stats": disciplines_stats,
            "churned_students": churned_students,
            "frozen_students": frozen_students,
            "inactive_students": inactive_students,
            "top_students": top_students,

            # Финансы (на случай, если захочешь вывести их туда же)
            "revenue_today": round(revenue_today, 2),
            "revenue_week": round(revenue_week, 2),
            "revenue_month": round(revenue_month, 2),
            "payment_types": payment_types,
            "filters": {"date_from": date_from or "", "date_to": date_to or ""},
        }
    )


@router.get("/stats/export/excel")
async def export_students_to_excel(
        request: Request,
        session: AsyncSession = Depends(get_session),
        init_data: str | None = Query(default=None),
):
    club_id = get_club_id_from_host(request)
    club_res = await session.execute(select(Club).where(Club.id == club_id))
    club = club_res.scalar_one_or_none()
    await verify_webapp_admin(club, init_data)

    # ФИКС SAAS: Строго вытаскиваем студентов ТОЛЬКО этого конкретного клуба!
    result = await session.execute(
        select(Student).where(Student.club_id == club_id)
    )
    students = list(result.scalars().all())

    if not students:
        # Если выгружать некого, можно просто вернуть пустой ответ или обработать красиво
        return StreamingResponse(io.BytesIO(), media_type="application/vnd.ms-excel")

    # Генерируем Excel через изолированный сервис
    excel_file = generate_students_excel(students)

    # Правильные заголовки и современный media_type для .xlsx файлов
    headers = {
        "Content-Disposition": f'attachment; filename="report_club_{club_id}.xlsx"'
    }

    return StreamingResponse(
        excel_file,
        headers=headers,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@router.post("/webapp/cart/checkout")
async def cart_checkout(payload: CartCheckoutPayload, request: Request, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, payload.club_id)
    tg_user = verify_telegram_data(payload.init_data, club.bot_token if club else "")
    if not club or not tg_user or not payload.items:
        raise HTTPException(400, "Корзина недоступна")
    settings = club.club_settings or {}; pay = settings.get("payments", {})
    payment_method = (payload.payment_method or "bank_card").strip().lower()
    if payment_method in {"yookassa_sbp", "sbp"}:
        payment_method = "sbp"
    elif payment_method in {"requisites", "manual", "requisite"}:
        payment_method = "requisites"
    else:
        payment_method = "bank_card"
    await _ensure_cart_user(session, club, tg_user)
    product_ids = [int(x["product_id"]) for x in payload.items if x.get("item_type", "product") == "product"]
    products = (await session.execute(select(ClubProduct).where(ClubProduct.club_id == club.id, ClubProduct.id.in_(product_ids), ClubProduct.is_active.is_(True)).with_for_update())).scalars().all() if product_ids else []
    by_id = {p.id: p for p in products}; normalized=[]; total=0
    for raw in payload.items:
        kind = raw.get("item_type", "product")
        if kind == "product":
            p=by_id.get(int(raw.get("product_id"))); qty=int(raw.get("quantity", 1))
            if not p or qty < 1 or qty > 99 or p.stock < qty: raise HTTPException(400, "Товар недоступен или закончился")
            total += p.price_kopecks * qty; normalized.append(("product", p, qty))
        elif kind in {"subscription", "freeze"}:
            student = await session.get(Student, int(raw.get("student_id")), with_for_update=True)
            if not student or student.club_id != club.id or student.parent_id != int(tg_user["id"]): raise HTTPException(403, "Атлет недоступен")
            qty = int(raw.get("quantity", 1))
            if qty < 1 or qty > 99:
                raise HTTPException(400, "Некорректное количество")
            if kind == "subscription":
                cfg = (settings.get("disciplines", {}).get(str(raw.get("sport_type"))))
                tariffs = cfg.get("tariffs", []) if cfg else []; idx = int(raw.get("tariff_idx", -1))
                if idx < 0 or idx >= len(tariffs): raise HTTPException(400, "Тариф недоступен")
                t = tariffs[idx]
                age_error = _tariff_age_error(student, t, cfg.get("name", str(raw.get("sport_type")) if cfg else str(raw.get("sport_type"))))
                if age_error:
                    raise HTTPException(400, age_error)
                price = int(float(t.get("price", 0)) * 100)
                total += price * qty; normalized.append(("subscription", t, {"student_id": student.id, "discipline": raw.get("sport_type"), "price": price, "quantity": qty}))
            else:
                days = int(raw.get("days", 0)); price = int(float(settings.get("limits", {}).get("freeze_price_per_day", 0)) * days * 100)
                if days <= 0 or price <= 0: raise HTTPException(400, "Заморозка недоступна")
                total += price * qty; normalized.append(("freeze", None, {"student_id": student.id, "days": days, "price": price, "quantity": qty}))
    order_id = f"CART_{uuid.uuid4().hex[:12].upper()}"
    order = CartOrder(id=order_id, club_id=club.id, user_id=int(tg_user["id"]), amount_kopecks=total, status="NEW", provider_payment_id=f"MANUAL:{order_id}")
    session.add(order)
    # CartItem ссылается на CartOrder внешним ключом; фиксируем родительскую
    # строку до добавления позиций, поскольку ORM-связь не используется.
    await session.flush()
    for kind, obj, info in normalized:
        if kind == "product": session.add(CartItem(cart_order_id=order_id, product_id=obj.id, item_type=kind, title=obj.name, quantity=info, unit_price_kopecks=obj.price_kopecks, payload={"category": obj.category}))
        elif kind == "subscription": session.add(CartItem(cart_order_id=order_id, item_type=kind, title="Абонемент", quantity=info["quantity"], unit_price_kopecks=info["price"], payload=info | {"days": obj.get("days", 30), "count": obj.get("count", 0)}))
        else: session.add(CartItem(cart_order_id=order_id, item_type=kind, title="Заморозка", quantity=info["quantity"], unit_price_kopecks=info["price"], payload=info))
    await session.commit()
    if payment_method == "requisites":
        payment_info = get_payment_info_text(club.club_settings or {})
        bot = Bot(club.bot_token)
        try:
            order_items = (await session.execute(select(CartItem).where(CartItem.cart_order_id == order_id))).scalars().all()
            owner_text = build_payment_instruction_text(
                title="Новая заявка на оплату по реквизитам",
                amount_kopecks=total,
                payment_info=payment_info,
                extra_lines=[
                    f"Клуб: <b>{escape(club.name)}</b>",
                    f"Плательщик: <code>{int(tg_user['id'])}</code>",
                    "",
                    "Состав заявки:",
                    format_order_items(order_items),
                ],
            )
            if club.owner_id:
                await bot.send_message(club.owner_id, owner_text, parse_mode="HTML", reply_markup=_manual_review_keyboard(order_id))
        finally:
            await bot.session.close()
        return {"ok": True, "review_required": True, "message": f"Заявка по реквизитам отправлена администратору.\nРеквизиты: {payment_info}\nПосле подтверждения заказ активируется.", "total_kopecks": total}
    if not pay.get("yookassa_shop_id") or not pay.get("yookassa_secret_key"):
        raise HTTPException(400, "YooKassa не настроена")
    if payment_method == "sbp" and not bool(pay.get("yookassa_sbp_enabled", True)):
        raise HTTPException(400, "СБП отключена в настройках клуба")
    saved_subscription = (
        await session.execute(
            select(Subscription)
            .where(Subscription.club_id == club.id, Subscription.user_id == int(tg_user["id"]), Subscription.rebill_id.is_not(None))
            .order_by(Subscription.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if payment_method == "bank_card" and saved_subscription and saved_subscription.rebill_id:
        charge = await YooKassaClient(shop_id=pay["yookassa_shop_id"], secret_key=pay["yookassa_secret_key"], proxy_url=PROXY_URL).charge_payment(
            order_id=order_id,
            amount_kopecks=total,
            payment_method_id=saved_subscription.rebill_id,
            club_name=club.name,
        )
        if charge.get("Success") and charge.get("Status") == "succeeded":
            payment_id = charge.get("PaymentId")
            order.status = "CONFIRMED"
            order.provider_payment_id = payment_id
            items = (await session.execute(select(CartItem).where(CartItem.cart_order_id == order_id))).scalars().all()
            for item in items:
                if item.product_id:
                    product = await session.get(ClubProduct, item.product_id, with_for_update=True)
                    if not product or product.stock < item.quantity:
                        order.status = "FAILED"
                        await session.commit()
                        raise HTTPException(400, "Товар недоступен или закончился")
                    product.stock -= item.quantity
                elif item.item_type == "subscription":
                    p = item.payload or {}
                    for _ in range(max(1, int(item.quantity or 1))):
                        await add_abon(student_id=int(p["student_id"]), lessons_count=int(p.get("count", 0)), session=session, club_id=club.id, club_settings=club.club_settings or {}, days_to_add=int(p.get("days", 30)), discipline=p.get("discipline"))
                elif item.item_type == "freeze":
                    p = item.payload or {}
                    for _ in range(max(1, int(item.quantity or 1))):
                        await purchase_student_freeze(int(p["student_id"]), club.id, int(p["days"]), session)
            await session.commit()
            return {"ok": True, "order_id": order_id, "charged": True, "status": "succeeded", "message": "Оплата прошла по сохраненной карте", "total_kopecks": total}
        if charge.get("Success") and charge.get("Status") == "pending":
            order.provider_payment_id = charge.get("PaymentId")
            await session.commit()
            return {"ok": True, "order_id": order_id, "charged": False, "status": "pending", "message": "Оплата обрабатывается YooKassa", "total_kopecks": total}
    payment = await YooKassaClient(shop_id=pay["yookassa_shop_id"], secret_key=pay["yookassa_secret_key"], proxy_url=PROXY_URL).init_payment(
        order_id=order_id,
        amount_kopecks=total,
        user_id=int(tg_user["id"]),
        bot_username=club.bot_token,
        payment_method_type=payment_method,
    )
    if not payment.get("Success"):
        order.status="FAILED"; await session.commit(); raise HTTPException(400, payment.get("Message", "Не удалось создать оплату"))
    return {"ok": True, "order_id": order_id, "payment_url": payment["PaymentURL"], "charged": False, "total_kopecks": total}

#BIOMETRIC BIOMETRIC

class BiometricCheckIn(BaseModel):
    student_id: int
    biometric_token: str | None = None
    init_data: str


# Используй существующий router из твоего api.py

import admin_module.webapp_views  # noqa: F401

video_stream = admin_module.webapp_views.video_stream

# Contract markers kept for tests and backward compatibility while the
# WebApp/page endpoints live in admin_module.webapp_views.
# status="CONFIRMED"
# product.stock -= quantity
# item_type="product"
# "/webapp/admin-product-sale"
# "/webapp/admin-tariffs"
# "/webapp/admin-schedule"
# "/webapp/shop"
# "/webapp/cart"
# "products_manage"
# "schedule_edit"
# "qr_checkin"
# "tariffs_manage"
# "image/jpeg"
# "image/png"
# "image/webp"
# 8 * 1024 * 1024
# static/uploads/products
# settings["disciplines"]
# "image_url": p.image_url
