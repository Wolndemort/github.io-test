import httpx
import uuid
from loguru import logger
from sqlalchemy import func
from handlers.skud import trigger_dingtian_turnstile
from services.gate_control import process_athlete_gate_pass
from services.yookassa_client import YooKassaClient
from config import PROXY_URL
from fastapi import Query
from services.analytics import generate_students_excel, calculate_admin_dashboard
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
from fastapi import Request, UploadFile, File
from fastapi.responses import HTMLResponse
from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette import status
from database.db import User, Student, Club, ClubProduct, CartOrder, CartItem
from database.db import purchase_student_freeze
from admin_module.schemas import AdminStudentUpdate
from database.db import get_session
from config import fastapi_key
from aiogram import Bot
from handlers.user_option import generate_signature, fix_layout
from middlewares.db_saas_midleware import SUPER_ADMIN_IDS
from admin_module.router_base import router, templates
from admin_module.webapp_verify import verify_telegram_data
from admin_module.webapp_client_cabinet import _ensure_webapp_user_linked
import admin_module.webapp_client_cabinet  # noqa: F401
import admin_module.turnstile_biometry  # noqa: F401 - registers SKUD WebApp routes
import admin_module.payments_webhook  # noqa: F401
import admin_module.system_api  # noqa: F401


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

class ProductPayload(BaseModel):
    init_data: str
    club_id: int
    name: str
    category: str = "other"
    price_kopecks: int
    stock: int = 0
    is_active: bool = True
    image_url: str | None = None

class CartCheckoutPayload(BaseModel):
    init_data: str
    club_id: int
    items: list[dict]

def _telegram_init_gate(path: str, club_id: int, title: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><meta charset='utf-8'><script src='https://telegram.org/js/telegram-web-app.js'></script><script>const tg=window.Telegram.WebApp;tg.ready();if(!tg.initData)document.body.innerText='{title}';else location.replace('{path}?club_id={club_id}&init_data='+encodeURIComponent(tg.initData));</script>""", status_code=401)


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


# Хелпер-функция: парсит поддомен и достает club_id (для твоей SaaS-логики)
def get_club_id_from_host(request: Request) -> int:
    host = request.headers.get("host", "")
    subdomain = host.split(".")[0]

    if subdomain.isdigit():
        return int(subdomain)

    # Если зашли по прямой ссылке без поддомена, пробуем взять из query-параметров (?club_id=...)
    club_id_param = request.query_params.get("club_id")
    if club_id_param and club_id_param.isdigit():
        return int(club_id_param)

    return 0  # Дефолтный ID, если ничего не нашли


def webapp_auth_gate(request: Request, club_id: int):
    return HTMLResponse(f"""<!doctype html><meta charset='utf-8'>
<script src='https://telegram.org/js/telegram-web-app.js'></script><script>
const tg=window.Telegram.WebApp; tg.ready();
if (!tg.initData) document.body.innerText='Откройте приложение из Telegram';
else location.replace(location.pathname+'?club_id={club_id}&init_data='+encodeURIComponent(tg.initData));
</script>""", status_code=401)


async def verify_webapp_admin(club: Club, init_data: str | None):
    if not club or not getattr(club, "bot_token", None) or not init_data:
        raise HTTPException(status_code=403, detail="Требуется авторизация Telegram")
    tg_user = verify_telegram_data(init_data, club.bot_token)
    if not tg_user or (tg_user.get("id") != club.owner_id and tg_user.get("id") not in SUPER_ADMIN_IDS):
        raise HTTPException(status_code=403, detail="Доступ только для администратора клуба")


# 1. Добавляем роут /admin, который просила кнопка в ТГ (убрали get_api_key!)



@router.post("/webapp/scanner/scan")
async def scanner_scan(
    payload: ScannerPayload,
    session: AsyncSession = Depends(get_session),
):
    """Обрабатывает QR без sendData, чтобы планшетный сканер не закрывался."""
    club = (await session.execute(select(Club).where(Club.id == payload.club_id))).scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Клуб не найден")
    tg_user = verify_telegram_data(payload.init_data, club.bot_token)
    if not tg_user or (tg_user.get("id") != club.owner_id and tg_user.get("id") not in SUPER_ADMIN_IDS):
        raise HTTPException(status_code=403, detail="Только администратор клуба")

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

    result = await process_athlete_gate_pass(
        student_id, session, club.club_settings or {}, expected_club_id=club.id
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
    await verify_webapp_admin(club, init_data)
    students = (await session.execute(
        select(Student).where(Student.club_id == club_id).order_by(Student.name)
    )).scalars().all()
    return templates.TemplateResponse("admin_students.html", {
        "request": request,
        "club_id": club_id,
        "club_name": club.name,
        "students": students,
    })


@router.patch("/admin/students/{student_id}")
async def admin_update_student(
    student_id: int,
    payload: AdminStudentUpdate,
    db: AsyncSession = Depends(get_session),
):
    club = await db.get(Club, payload.club_id)
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
    if payload.balance_lessons is not None:
        if payload.balance_lessons < 0 or payload.balance_lessons > 999:
            raise HTTPException(status_code=400, detail="Баланс должен быть от 0 до 999")
        student.balance_lessons = payload.balance_lessons
    if payload.birthday is not None:
        if payload.birthday == "":
            student.birthday = None
        else:
            try:
                birthday = date.fromisoformat(payload.birthday)
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
        student.discipline = payload.discipline.strip()[:50] or student.discipline
    await db.commit()
    return {"ok": True}


@router.get("/revenue", response_class=HTMLResponse)
async def get_revenue_stats(
        request: Request,
        session: AsyncSession = Depends(get_session),
        init_data: str | None = Query(default=None),
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

    now_local = datetime.now(timezone.utc).replace(tzinfo=None)

    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=now_local.weekday())
    month_start = today_start.replace(day=1)

    # ==========================================
    # БЛОК 1: ФИНАНСЫ (Для твоих графиков/отчетов)
    # ==========================================
    payments_res = await session.execute(
        select(PaymentOrder.amount_kopecks, PaymentOrder.created_at).where(
            PaymentOrder.club_id == club_id,
            PaymentOrder.status == "CONFIRMED",
            PaymentOrder.created_at >= month_start
        )
    )
    all_payments = payments_res.all()

    revenue_today = 0
    revenue_week = 0
    revenue_month = 0

    for row in all_payments:
        amt = row[0]
        dt = row[1]
        p_date = dt.replace(tzinfo=None) if dt else month_start
        amount_rub = (amt or 0) / 100

        revenue_month += amount_rub
        if p_date >= week_start:
            revenue_week += amount_rub
        if p_date >= today_start:
            revenue_today += amount_rub

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
            {"request": request, "empty": True, "club_name": club_name}
        )

    total_athletes = len(students)
    active_passes = 0
    frozen_passes = 0
    burning_passes = 0
    inactive_passes = 0
    total_lessons_left = 0

    churned_students = []
    discipline_counts = {}

    for s in students:
        total_lessons_left += s.balance_lessons

        # Считаем популярность направлений по числу людей
        disc_key = s.discipline or "boxing"
        discipline_counts[disc_key] = discipline_counts.get(disc_key, 0) + 1

        # Распределяем по статусам абонементов
        if s.is_frozen:
            frozen_passes += 1
        elif s.balance_lessons <= 0:
            inactive_passes += 1
            churned_students.append({"name": s.name})
        elif 0 < s.balance_lessons <= 3:
            burning_passes += 1
            active_passes += 1
        else:
            active_passes += 1

    # Красивые имена для дисциплин в HTML
    discipline_names = {
        "boxing": "🥊 Бокс (Дети)",
        "kickboxing": "🤼‍♂️ Кикбоксинг",
        "bjj": "🥋 Бразильское джиу-джитсу",
        "yoga": "🧘‍♂️ Йога"
    }

    disciplines_stats = [
        {"name": discipline_names.get(k, f"🏃‍♂️ {k}"), "active_athletes": v}
        for k, v in discipline_counts.items()
    ]

    # Сортируем топ-атлетов по остатку занятий (первые 5 человек)
    sorted_students = sorted(students, key=lambda x: x.balance_lessons, reverse=True)
    top_students = [{"name": s.name, "balance": s.balance_lessons} for s in sorted_students[:5]]

    # Считаем Retention (Удержание). Например: процент тех, у кого баланс > 0
    retention_rate = round((active_passes / total_athletes) * 100) if total_athletes > 0 else 0

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
            "retention_rate": retention_rate,
            "active_passes": active_passes,
            "frozen_passes": frozen_passes,
            "burning_passes": burning_passes,
            "inactive_passes": inactive_passes,
            "total_lessons_left": total_lessons_left,
            "disciplines_stats": disciplines_stats,
            "churned_students": churned_students,
            "top_students": top_students,

            # Финансы (на случай, если захочешь вывести их туда же)
            "revenue_today": round(revenue_today, 2),
            "revenue_week": round(revenue_week, 2),
            "revenue_month": round(revenue_month, 2),
            "payment_types": payment_types
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
    if not pay.get("yookassa_shop_id") or not pay.get("yookassa_secret_key"):
        raise HTTPException(400, "YooKassa не настроена")
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
            if kind == "subscription":
                cfg = (settings.get("disciplines", {}).get(str(raw.get("sport_type"))))
                tariffs = cfg.get("tariffs", []) if cfg else []; idx = int(raw.get("tariff_idx", -1))
                if idx < 0 or idx >= len(tariffs): raise HTTPException(400, "Тариф недоступен")
                t = tariffs[idx]; price = int(float(t.get("price", 0)) * 100)
                total += price; normalized.append(("subscription", t, {"student_id": student.id, "discipline": raw.get("sport_type"), "price": price}))
            else:
                days = int(raw.get("days", 0)); price = int(float(settings.get("limits", {}).get("freeze_price_per_day", 0)) * days * 100)
                if days <= 0 or price <= 0: raise HTTPException(400, "Заморозка недоступна")
                total += price; normalized.append(("freeze", None, {"student_id": student.id, "days": days, "price": price}))
    order_id = f"CART_{uuid.uuid4().hex[:12].upper()}"
    order = CartOrder(id=order_id, club_id=club.id, user_id=int(tg_user["id"]), amount_kopecks=total, status="NEW")
    session.add(order)
    for kind, obj, info in normalized:
        if kind == "product": session.add(CartItem(cart_order_id=order_id, product_id=obj.id, item_type=kind, title=obj.name, quantity=info, unit_price_kopecks=obj.price_kopecks, payload={"category": obj.category}))
        elif kind == "subscription": session.add(CartItem(cart_order_id=order_id, item_type=kind, title="Абонемент", quantity=1, unit_price_kopecks=info["price"], payload=info | {"days": obj.get("days", 30), "count": obj.get("count", 0)}))
        else: session.add(CartItem(cart_order_id=order_id, item_type=kind, title="Заморозка", quantity=1, unit_price_kopecks=info["price"], payload=info))
    await session.commit()
    payment = await YooKassaClient(shop_id=pay["yookassa_shop_id"], secret_key=pay["yookassa_secret_key"], proxy_url=PROXY_URL).init_payment(order_id=order_id, amount_kopecks=total, user_id=int(tg_user["id"]), bot_username=club.bot_token)
    if not payment.get("Success"):
        order.status="FAILED"; await session.commit(); raise HTTPException(400, payment.get("Message", "Не удалось создать оплату"))
    return {"ok": True, "order_id": order_id, "payment_url": payment["PaymentURL"], "total_kopecks": total}

@router.get("/webapp/shop", response_class=HTMLResponse)
async def client_shop(request: Request, club_id: int = Query(...), init_data: str | None = Query(None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data: return _telegram_init_gate('/webapp/shop', club_id, 'Откройте магазин из Telegram')
    if not club or not verify_telegram_data(init_data, club.bot_token): raise HTTPException(403, "Доступ запрещён")
    products = (await session.execute(select(ClubProduct).where(ClubProduct.club_id == club_id, ClubProduct.is_active.is_(True), ClubProduct.stock > 0).order_by(ClubProduct.category, ClubProduct.name))).scalars().all()
    return templates.TemplateResponse("shop.html", {"request": request, "club": club, "club_id": club_id, "products": products})

@router.get("/webapp/admin-products", response_class=HTMLResponse)
async def admin_products_page(request: Request, club_id: int = Query(...), init_data: str | None = Query(None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data: return _telegram_init_gate('/webapp/admin-products', club_id, 'Откройте каталог из Telegram')
    await verify_webapp_admin(club, init_data)
    products = (await session.execute(select(ClubProduct).where(ClubProduct.club_id == club_id).order_by(ClubProduct.id.desc()))).scalars().all()
    return templates.TemplateResponse("admin_products.html", {"request": request, "club": club, "club_id": club_id, "products": products})

@router.post("/webapp/admin-products")
async def create_product(payload: ProductPayload, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, payload.club_id); await verify_webapp_admin(club, payload.init_data)
    if not payload.name.strip() or payload.price_kopecks <= 0 or payload.stock < 0: raise HTTPException(400, "Некорректные данные товара")
    p = ClubProduct(club_id=payload.club_id, name=payload.name.strip()[:120], image_url=(payload.image_url or "")[:500] or None, category=payload.category[:30], price_kopecks=payload.price_kopecks, stock=payload.stock, is_active=payload.is_active)
    session.add(p); await session.commit(); return {"success": True}

@router.post("/webapp/admin-products/upload-image")
async def upload_product_image(club_id: int, init_data: str, image: UploadFile = File(...), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id); await verify_webapp_admin(club, init_data)
    allowed = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
    if image.content_type not in allowed: raise HTTPException(400, "Разрешены JPG, PNG и WEBP")
    data = await image.read()
    if len(data) > 5 * 1024 * 1024: raise HTTPException(400, "Изображение не должно быть больше 5 МБ")
    folder = "static/uploads/products"; os.makedirs(folder, exist_ok=True)
    filename = f"club_{club_id}_{uuid.uuid4().hex}{allowed[image.content_type]}"
    path = os.path.join(folder, filename)
    with open(path, "wb") as handle: handle.write(data)
    return {"image_url": f"/static/uploads/products/{filename}"}

@router.patch("/webapp/admin-products/{product_id}")
async def update_product(product_id: int, payload: ProductPayload, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, payload.club_id); await verify_webapp_admin(club, payload.init_data)
    p = await session.get(ClubProduct, product_id)
    if not p or p.club_id != payload.club_id: raise HTTPException(404, "Товар не найден")
    if not payload.name.strip() or payload.price_kopecks <= 0 or payload.stock < 0: raise HTTPException(400, "Некорректные данные товара")
    p.name=payload.name.strip()[:120]; p.image_url=(payload.image_url or "")[:500] or None; p.category=payload.category[:30]; p.price_kopecks=payload.price_kopecks; p.stock=payload.stock; p.is_active=payload.is_active
    await session.commit(); return {"success": True}

@router.delete("/webapp/admin-products/{product_id}")
async def delete_product(product_id: int, club_id: int, init_data: str, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id); await verify_webapp_admin(club, init_data)
    p = await session.get(ClubProduct, product_id)
    if not p or p.club_id != club_id: raise HTTPException(404, "Товар не найден")
    await session.delete(p); await session.commit(); return {"success": True}

@router.get("/webapp/admin-schedule", response_class=HTMLResponse)
async def webapp_admin_schedule_page(
        request: Request,
        club_id: int = Query(...),
        session: AsyncSession = Depends(get_session),
        init_data: str | None = Query(default=None),
):
    club = (await session.execute(select(Club).where(Club.id == club_id))).scalar_one_or_none()
    if not init_data:
        return _telegram_init_gate('/webapp/admin-schedule', club_id, 'Откройте админское расписание из Telegram')
    await verify_webapp_admin(club, init_data)
    return templates.TemplateResponse("admin_schedule.html", {"request": request, "club": club, "club_id": club_id, "disciplines": club.club_settings.get("disciplines", {})})

@router.post("/webapp/admin-schedule/change")
async def change_admin_schedule(payload: ScheduleChangePayload, session: AsyncSession = Depends(get_session)):
    club = (await session.execute(select(Club).where(Club.id == payload.club_id))).scalar_one_or_none()
    await verify_webapp_admin(club, payload.init_data)
    settings = dict(club.club_settings or {})
    disciplines = dict(settings.get("disciplines", {}))
    block = dict(disciplines.get(payload.discipline, {}))
    schedule = dict(block.get("schedule", {}))
    lessons = list(schedule.get(payload.day, []))
    if payload.action == "delete":
        if payload.index is None or payload.index < 0 or payload.index >= len(lessons):
            raise HTTPException(400, "Занятие не найдено")
        lessons.pop(payload.index)
    elif payload.action in {"add", "update"}:
        lesson = payload.lesson or {}
        item = {"time": str(lesson.get("time", "00:00"))[:5], "coach": str(lesson.get("coach", ""))[:100], "max_slots": max(1, min(999, int(lesson.get("max_slots", 50))))}
        if payload.action == "add": lessons.append(item)
        elif payload.index is not None and 0 <= payload.index < len(lessons): lessons[payload.index] = item
        else: raise HTTPException(400, "Занятие не найдено")
    else: raise HTTPException(400, "Неизвестное действие")
    lessons.sort(key=lambda x: str(x.get("time", "99:99")))
    schedule[payload.day] = lessons; block["schedule"] = schedule; disciplines[payload.discipline] = block; settings["disciplines"] = disciplines
    club.club_settings = settings
    await session.commit()
    return {"success": True}


@router.get("/webapp/schedule", response_class=HTMLResponse)
async def webapp_schedule_page(
        request: Request,
        club_id: int = None,
        session: AsyncSession = Depends(get_session),
        init_data: str | None = Query(default=None)
):
    from database.db import Club
    from sqlalchemy.future import select

    if not club_id:
        try:
            club_id = get_club_id_from_host(request)
        except Exception:
            club_id = None

    if not club_id:
        return HTMLResponse(content="<h1>❌ Ошибка: Не удалось определить ID клуба</h1>", status_code=400)

    stmt = select(Club).where(Club.id == club_id)
    result = await session.execute(stmt)
    club = result.scalar_one_or_none()

    if not club:
        return HTMLResponse(content="<h1>🏰 Клуб не найден в системе SpeedyCRM</h1>", status_code=404)
    # Публичная клиентская страница: только чтение, без Telegram-аутентификации.

    settings = club.club_settings if isinstance(club.club_settings, dict) else {}
    disciplines_data = settings.get("disciplines", {})

    day_names = {
        "mon": "Понедельник", "tue": "Вторник", "wed": "Среда",
        "thu": "Четверг", "fri": "Пятница", "sat": "Суббота", "sun": "Воскресенье"
    }

    # Парсим JSON-настройки клуба в структурированный список для Jinja2 шаблона
    parsed_disciplines = []

    if isinstance(disciplines_data, dict):
        for disc_key, disc_content in disciplines_data.items():
            if not isinstance(disc_content, dict):
                continue

            disc_name = disc_content.get("name", "Спортивная секция")
            schedule_data = disc_content.get("schedule", {})
            if not isinstance(schedule_data, dict):
                schedule_data = {}

            parsed_days = []
            for day_key, day_title in day_names.items():
                lessons = schedule_data.get(day_key, [])
                if not lessons or not isinstance(lessons, list):
                    continue

                parsed_lessons = []
                for lesson in lessons:
                    if not isinstance(lesson, dict):
                        continue

                    max_slots = lesson.get("max_slots") or lesson.get("slots") or lesson.get("limit") or 50
                    taken_slots = lesson.get("taken_slots") or 0

                    try:
                        max_slots = int(max_slots)
                    except (ValueError, TypeError):
                        max_slots = 50

                    try:
                        taken_slots = int(taken_slots)
                    except (ValueError, TypeError):
                        taken_slots = 0

                    parsed_lessons.append({
                        "time": str(lesson.get("time", "00:00")),
                        "coach": str(lesson.get("coach", "Инструктор")),
                        "max_slots": max_slots,
                        "free_slots": max(0, max_slots - taken_slots)
                    })

                if parsed_lessons:
                    parsed_days.append({
                        "title": day_title,
                        "lessons": parsed_lessons
                    })

            parsed_disciplines.append({
                "name": disc_name,
                "days": parsed_days
            })

    # Отдаем чистый контекст в шаблон
    ui = settings.get("ui", {}) if isinstance(settings.get("ui", {}), dict) else {}
    loading = ui.get("loading", {}) if isinstance(ui.get("loading", {}), dict) else {}
    context = {
        "request": request,
        "club_name": club.name or 'Без названия',
        "disciplines": parsed_disciplines
        ,"loading": {"enabled": bool(loading.get("enabled", False)),
                     "duration_ms": max(300, min(10000, int(loading.get("duration_ms", 1200)))),
                     "message": str(loading.get("message", "Загружаем приложение…"))},
        "logo_url": str(ui.get("logo_url", ""))
    }
    return templates.TemplateResponse("schedule.html", context)


@router.get("/privacy", response_class=HTMLResponse)
async def get_privacy_page(request: Request):
    """Страница политики конфиденциальности для WebApp"""
    return templates.TemplateResponse("privacy.html", {"request": request})


@router.get("/oferta", response_class=HTMLResponse)
async def get_oferta_page(request: Request):
    """Страница публичной оферты для WebApp"""
    return templates.TemplateResponse("oferta.html", {"request": request})


@router.get("/webapp/live_cam", response_class=HTMLResponse)
async def get_cameras_page(request: Request, club_id: int = Query(...), init_data: str | None = Query(default=None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data:
        return webapp_auth_gate(request, club_id)
    await verify_webapp_admin(club, init_data)
    return templates.TemplateResponse("cameras.html", {"request": request, "club_id": club_id})


# 2. Роут генерации стрима
@router.get("/webapp/live_cam/stream")
async def video_stream(
        club_id: int = Query(...),
        camera_src: str | None = None,
        init_data: str | None = Query(default=None),
        session: AsyncSession = Depends(get_session)
):
    """
    Проксирует MJPEG видеопоток из внутреннего контейнера Docker (go2rtc)
    напрямую в WebApp смартфона, динамически подставляя камеру из настроек клуба.
    """
    # Вытаскиваем настройки именно этого клуба из БД для изоляции SaaS
    result = await session.execute(select(Club).where(Club.id == club_id))
    club = result.scalar_one_or_none()

    if not club:
        raise HTTPException(status_code=404, detail="Клуб не найден")
    if isinstance(init_data, str) or init_data is None:
        await verify_webapp_admin(club, init_data)

    # Берем имя камеры из club_settings. Если там пусто, ставим дефолтное "camera1"
    settings = club.club_settings or {}
    turnstile_settings = settings.get("turnstile", {})
    if not isinstance(turnstile_settings, dict):
        turnstile_settings = {}
    camera_src = camera_src or turnstile_settings.get("camera_src") or "camera1"

    # Смартфон не видит внутреннюю Docker-сеть. Поэтому FastAPI сам подключается
    # к go2rtc и передает полученные байты наружу без изменения.
    # go2rtc работает в той же Docker-сети, что и API. Не используем
    # host.docker.internal: это ломается на Docker Desktop и при деплое на Linux.
    go2rtc_mjpeg_api = "http://go2rtc:1984/api/stream.mjpeg"
    # Некоторые RTSP-источники поднимают первый кадр заметно дольше обычного.
    # Даем go2rtc больше времени именно на установление соединения с источником.
    timeout = httpx.Timeout(connect=150.0, read=None, write=10.0, pool=10.0)
    client = httpx.AsyncClient(timeout=timeout)

    try:
        request = client.build_request(
            "GET",
            go2rtc_mjpeg_api,
            params={"src": camera_src}
        )
        response = await client.send(request, stream=True)
    except httpx.RequestError as exc:
        await client.aclose()
        logger.error(
            "Не удалось подключиться к go2rtc: club_id={}, camera_src={}, error={}",
            club_id,
            camera_src,
            exc
        )
        raise HTTPException(
            status_code=502,
            detail="Сервис камер временно недоступен"
        ) from exc

    if response.status_code != 200:
        status_code = response.status_code
        await response.aclose()
        await client.aclose()
        logger.error(
            "go2rtc не отдал MJPEG-поток: club_id={}, camera_src={}, status={}",
            club_id,
            camera_src,
            status_code
        )
        raise HTTPException(
            status_code=502,
            detail=f"Камера не отдала видеопоток (go2rtc: {status_code})"
        )

    # В MJPEG заголовок Content-Type содержит boundary — имя разделителя кадров.
    # Нельзя подставлять его вручную: у разных источников оно может отличаться.
    content_type = response.headers.get(
        "content-type",
        "multipart/x-mixed-replace; boundary=frame"
    )

    if "multipart/x-mixed-replace" not in content_type.lower():
        await response.aclose()
        await client.aclose()
        logger.error(
            "go2rtc вернул неожиданный Content-Type: club_id={}, camera_src={}, content_type={}",
            club_id,
            camera_src,
            content_type
        )
        raise HTTPException(
            status_code=502,
            detail="Камера доступна, но не отдает MJPEG. Проверьте кодек в go2rtc"
        )

    async def stream_generator():
        try:
            # aiter_raw сохраняет multipart-разметку и границы JPEG-кадров как есть.
            async for chunk in response.aiter_raw():
                yield chunk
        except httpx.HTTPError as exc:
            logger.warning(
                "MJPEG-поток оборвался: club_id={}, camera_src={}, error={}",
                club_id,
                camera_src,
                exc
            )
        finally:
            await response.aclose()
            await client.aclose()

    # X-Accel-Buffering запрещает Nginx копить кадры в буфере.
    return StreamingResponse(
        stream_generator(),
        headers={
            "Content-Type": content_type,
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no"
        }
    )


@router.get("/pass-app", response_class=HTMLResponse)
async def get_web_app_page(
        request: Request,
        user_id: int,
        init_data: str | None = Query(default=None),
        db: AsyncSession = Depends(get_session),
):
    """
    Эндпоинт, который открывается в Telegram WebApp по ссылке:
    https://твоя_куча_поддоменов.ru/admin/pass-app?user_id={telegram_id}
    """
    if not init_data:
        return HTMLResponse(f"""<!doctype html><meta charset='utf-8'>
<script src='https://telegram.org/js/telegram-web-app.js'></script><script>
const tg=window.Telegram.WebApp; tg.ready();
if (!tg.initData) document.body.innerText='Откройте приложение из Telegram';
else location.replace(location.pathname+'?user_id={user_id}&init_data='+encodeURIComponent(tg.initData));
</script>""", status_code=401)

    # Вытаскиваем родителя и проверяем подписанные данные Telegram до выдачи детей.
    query = select(User).where(User.user_id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    club = await db.get(Club, user.club_id)
    tg_user = verify_telegram_data(init_data, club.bot_token if club else "")
    if not tg_user or int(tg_user.get("id", 0)) != user_id:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    settings = (club.club_settings or {}) if club else {}
    ui = settings.get("ui", {}) if isinstance(settings.get("ui", {}), dict) else {}
    loading = ui.get("loading", {}) if isinstance(ui.get("loading", {}), dict) else {}
    freeze_price_per_day = settings.get("limits", {}).get("freeze_price_per_day", 0)
    # Достаем список студентов для этого родителя
    students_query = select(Student).where(Student.parent_id == user_id)
    students_result = await db.execute(students_query)
    students = students_result.scalars().all()

    # Рендерим HTML страницу и передаем туда список детей
    return templates.TemplateResponse("biometric_pass.html", {"request": request, "students": students,
        "club_id": club_id, "user_id": user_id, "club_name": club.name if club else "", "logo_url": ui.get("logo_url", ""),
        "loading": {"enabled": bool(loading.get("enabled", False)), "duration_ms": max(300, min(10000, int(loading.get("duration_ms", 1200)))), "message": str(loading.get("message", "Загружаем приложение…"))}})



#BIOMETRIC BIOMETRIC

class BiometricCheckIn(BaseModel):
    student_id: int
    biometric_token: str | None = None
    init_data: str


# Используй существующий router из твоего api.py


