from __future__ import annotations

import asyncio
import copy
import calendar
import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from html import escape
from urllib.parse import quote

import httpx
from io import BytesIO
from PIL import Image, ImageOps, UnidentifiedImageError
from aiogram import Bot, types
from fastapi import Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import String, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from admin_module.router_base import router, templates
from admin_module.api import (
    AdminProductSalePayload,
    CashEntryPayload,
    CashSaleDeletePayload,
    ProductPayload,
    ProductCategoryChangePayload,
    ScheduleChangePayload,
    TariffChangePayload,
    DiscountChangePayload,
)
from admin_module.utils import verify_webapp_admin, verify_webapp_staff
from admin_module.webapp_shared import get_club_id_from_host, telegram_init_gate, webapp_auth_gate, verify_webapp_admin
from admin_module.webapp_verify import verify_telegram_data
from database.db import Club, ClubProduct, ClubStaff, Discount, DiscountAssignment, MotivationAccrual, PaymentOrder, Student, User, VisitLog, Survey, SurveyQuestion, SurveyDelivery, SurveyAnswer, get_session, get_student_parent_ids
from database.db import CartItem, CartOrder
from services.audit import audit_event
from services.broadcast import broadcast_recipients_for_student_search, resolve_broadcast_recipients
from services.delivery import claim_once, forget, mark_once, release_claim, telegram_call, telegram_pace
from services.surveys import _matches_filters, send_survey_to_students
from services.analytics import is_subscription_active, reporting_periods
from services.legal_documents import legal_context, legal_documents_enabled, validate_legal_data
from services.platform_legal import platform_legal_context
from admin_module.api import audit_actor_context
from admin_module.api import (
    build_owner_receipt_text,
    build_staff_alert_text,
    format_order_items,
    notify_product_staff,
)
from services.order_notifications import resolve_user_label
from services.schedule_utils import normalize_schedule_block
from services.payment_requisites import get_payment_info_text
from services.discounts import active_discounts, apply_discounts
from services.availability import payment_availability
from services.staff_permissions import staff_can
from services.motivation_schedule import (
    MOTIVATION_TZ as _MOTIVATION_TZ,
    local_date as _motivation_local_date,
    motivation_bonus as _fixed_motivation_bonus,
    motivation_occurrences as _motivation_occurrences,
    occurrence_ended as _motivation_occurrence_ended,
    remember_schedule_change as _remember_motivation_schedule_change,
)
from middlewares.db_saas_midleware import SUPER_ADMIN_IDS

def _normalize_category(value: str | None) -> str:
    return " ".join((value or "other").strip().casefold().replace("ё", "е").split()) or "other"


async def _discount_staff_recipient_ids(session: AsyncSession, club: Club) -> set[int]:
    rows = (await session.execute(select(ClubStaff).where(ClubStaff.club_id == club.id, ClubStaff.is_active.is_(True)))).scalars().all()
    ids = {int(club.owner_id)} if club.owner_id else set()
    for staff in rows:
        permissions = staff.permissions if isinstance(staff.permissions, dict) else {}
        if staff.role in {"manager", "cashier"} or permissions.get("cash_sale") or permissions.get("broadcast"):
            if staff.telegram_id:
                ids.add(int(staff.telegram_id))
    return ids

async def _canonical_product_category(session: AsyncSession, club_id: int, value: str | None, *, exclude_id: int | None = None) -> str:
    raw = " ".join((value or "other").strip().split()) or "other"
    wanted = _normalize_category(raw)
    query = select(ClubProduct).where(ClubProduct.club_id == club_id)
    if exclude_id is not None:
        query = query.where(ClubProduct.id != exclude_id)
    products = (await session.execute(query.order_by(ClubProduct.id.asc()))).scalars().all()
    for product in products:
        existing = " ".join((product.category or "other").strip().split()) or "other"
        if _normalize_category(existing) == wanted:
            return existing
    return raw[:30]

def _build_category_list(products):
    labels = {}
    for product in products:
        raw = (getattr(product, "category", None) or "other").strip() or "other"
        labels.setdefault(_normalize_category(raw), raw)
    return [labels[key] for key in sorted(labels)]

def _attendee_count(visit_rows, occurrence):
    try:
        hour, minute = (int(x) for x in occurrence["time"].split(":", 1))
    except (ValueError, AttributeError):
        return 0
    slot_minutes = hour * 60 + minute
    students = set()
    for visit, discipline in visit_rows:
        if str(discipline or "") != str(occurrence["discipline"]):
            continue
        visited = visit.visited_at
        if not visited or _motivation_local_date(visited) != occurrence["date"]:
            continue
        local_visited = visited.replace(tzinfo=timezone.utc).astimezone(_MOTIVATION_TZ) if visited.tzinfo is None else visited.astimezone(_MOTIVATION_TZ)
        duration = max(15, min(240, int(occurrence.get("duration_minutes", 60) or 60)))
        visit_minutes = local_visited.hour * 60 + local_visited.minute
        if slot_minutes - 60 <= visit_minutes <= slot_minutes + duration + 60:
            students.add(visit.student_id)
    return len(students)

def _scheduled_rate(rate_row, training_date, fallback=0):
    if not rate_row:
        return int(fallback or 0)
    specific = rate_row.weekend_rate_kopecks if training_date.weekday() >= 5 else rate_row.weekday_rate_kopecks
    return int(specific or rate_row.rate_kopecks or 0)

def _motivation_bonus(rate_row, student_count):
    return _fixed_motivation_bonus(rate_row, student_count)

def _individual_motivation_occurrence(item):
    return {
        "date": item.training_date,
        "time": item.start_time,
        "duration_minutes": item.duration_minutes,
    }

def _parse_motivation_time(value):
    text = str(value or "").strip()
    try:
        hour, minute = (int(part) for part in text.split(":", 1))
    except (TypeError, ValueError):
        raise HTTPException(400, "Время должно быть в формате ЧЧ:ММ")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise HTTPException(400, "Время должно быть в формате ЧЧ:ММ")
    return f"{hour:02d}:{minute:02d}"

def _product_financials(product):
    """Potential figures for the current stock, all represented in kopecks."""
    stock = max(0, int(product.stock or 0))
    sale_price = max(0, int(product.price_kopecks or 0))
    purchase_price = max(0, int(product.purchase_price_kopecks or 0))
    return {
        "stock_value_kopecks": sale_price * stock,
        "potential_profit_kopecks": (sale_price - purchase_price) * stock,
    }

def _product_totals(products):
    totals = {"stock_value_kopecks": 0, "potential_profit_kopecks": 0, "purchase_value_kopecks": 0}
    for product in products:
        figures = _product_financials(product)
        totals["stock_value_kopecks"] += figures["stock_value_kopecks"]
        totals["potential_profit_kopecks"] += figures["potential_profit_kopecks"]
        totals["purchase_value_kopecks"] += max(0, int(product.purchase_price_kopecks or 0)) * max(0, int(product.stock or 0))
    return totals

@router.get("/webapp/shop", response_class=HTMLResponse)
async def client_shop(request: Request, club_id: int = Query(...), init_data: str | None = Query(None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data: return telegram_init_gate('/webapp/shop', club_id, 'РћС‚РєСЂРѕР№С‚Рµ РјР°РіР°Р·РёРЅ РёР· Telegram')
    if not club or not verify_telegram_data(init_data, club.bot_token): raise HTTPException(403, "Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰С‘РЅ")
    products = (await session.execute(select(ClubProduct).where(ClubProduct.club_id == club_id, ClubProduct.is_active.is_(True), ClubProduct.stock > 0).order_by(ClubProduct.category, ClubProduct.name))).scalars().all()
    product_data = [{"id": p.id, "name": p.name, "category": p.category, "price_kopecks": p.price_kopecks, "stock": p.stock, "image_url": p.image_url, "details": p.details} for p in products]
    categories = _build_category_list(products)
    payment_modes = payment_availability(club.club_settings)
    payment_info = get_payment_info_text(club.club_settings or {})
    return templates.TemplateResponse("shop.html", {"request": request, "club": club, "club_id": club_id, "products": product_data, "categories": categories, "payment_modes": payment_modes, "sbp_enabled": payment_modes["sbp"], "online_enabled": payment_modes["online"], "payment_info": payment_info})


@router.get("/webapp/cart", response_class=HTMLResponse)
async def client_cart(request: Request, club_id: int = Query(...), init_data: str | None = Query(None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data:
        return telegram_init_gate('/webapp/cart', club_id, 'РћС‚РєСЂРѕР№С‚Рµ РєРѕСЂР·РёРЅСѓ РёР· Telegram')
    if not club or not verify_telegram_data(init_data, club.bot_token):
        raise HTTPException(403, "Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰С‘РЅ")
    payment_modes = payment_availability(club.club_settings)
    payment_info = get_payment_info_text(club.club_settings or {})
    return templates.TemplateResponse("cart.html", {"request": request, "club": club, "club_id": club_id, "payment_modes": payment_modes, "sbp_enabled": payment_modes["sbp"], "online_enabled": payment_modes["online"], "payment_info": payment_info})

def _broadcast_disciplines(club: Club, students: list[Student] | None = None) -> list[dict]:
    settings = club.club_settings if isinstance(club.club_settings, dict) else {}
    configured = settings.get("disciplines", {}) if isinstance(settings.get("disciplines", {}), dict) else {}
    codes = set(configured)
    codes.update(str(student.discipline) for student in (students or []) if student.discipline)
    rows = []
    for code in sorted(codes):
        item = configured.get(code) if isinstance(configured.get(code), dict) else {}
        rows.append({"code": code, "name": item.get("name") or code, "active": bool(item.get("active", True))})
    return rows


@router.get("/webapp/admin-broadcast", response_class=HTMLResponse)
async def admin_broadcast_page(request: Request, club_id: int = Query(...), init_data: str | None = Query(None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data:
        return telegram_init_gate('/webapp/admin-broadcast', club_id, 'Откройте рассылку из Telegram')
    await verify_webapp_staff(club, init_data, session, "broadcast")
    discounts = (await session.scalars(select(Discount).where(Discount.club_id == club_id, Discount.is_active.is_(True)).order_by(Discount.name))).all()
    return templates.TemplateResponse("admin_broadcast.html", {"request": request, "club": club, "club_id": club_id, "discounts": discounts})


@router.get("/webapp/admin-broadcast/students")
async def admin_broadcast_students(
    club_id: int,
    q: str = Query(default="", max_length=120),
    discipline: str = Query(default="all", max_length=50),
    init_data: str = Query(default=""),
    session: AsyncSession = Depends(get_session),
):
    club = await session.get(Club, club_id)
    await verify_webapp_staff(club, init_data, session, "broadcast")
    filters = [Student.club_id == club_id]
    needle = q.strip()
    if needle:
        pattern = f"%{needle}%"
        filters.append(or_(
            Student.name.ilike(pattern),
            Student.parent_phone.ilike(pattern),
            Student.parent_phone_secondary.ilike(pattern),
            User.full_name.ilike(pattern),
        ))
    if discipline and discipline != "all":
        filters.append(Student.discipline == discipline)
    students = list((await session.scalars(
        select(Student).outerjoin(User, User.user_id == Student.parent_id)
        .where(*filters).order_by(Student.name).limit(500)
    )).all())
    rows = []
    for student in students:
        recipients = await resolve_broadcast_recipients(session, club_id, [student.id])
        rows.append({
            "id": student.id,
            "name": student.name,
            "discipline": student.discipline or "",
            "recipient_count": len(recipients),
            "phone": student.parent_phone or student.parent_phone_secondary or "",
            "active_subscription": is_subscription_active(student),
        })
    return {"students": rows, "disciplines": _broadcast_disciplines(club, students), "total": len(rows)}


@router.post("/webapp/admin-broadcast/send")
async def admin_broadcast_send(
    club_id: int = Form(...),
    init_data: str = Form(...),
    student_ids: str = Form(...),
    text: str = Form(default=""),
    idempotency_key: str = Form(...),
    discount_id: int | None = Form(default=None),
    media: UploadFile | None = File(default=None),
    session: AsyncSession = Depends(get_session),
):
    club = await session.get(Club, club_id)
    tg_user = await verify_webapp_staff(club, init_data, session, "broadcast")
    operation_key = str(idempotency_key or "").strip()[:120]
    if len(operation_key) < 16:
        raise HTTPException(400, "Нужен idempotency_key для рассылки")
    claim_key = f"broadcast:operation:{club_id}:{int(tg_user['id'])}:{operation_key}"
    claim_token = await claim_once(claim_key, ttl=15 * 60)
    if not claim_token:
        raise HTTPException(409, "Эта рассылка уже выполняется или была отправлена")
    completed = False
    try:
        try:
            selected_ids = [int(value) for value in json.loads(student_ids)]
        except (TypeError, ValueError, json.JSONDecodeError):
            raise HTTPException(400, "Некорректный список получателей")
        selected_ids = list(dict.fromkeys(selected_ids))
        if not selected_ids:
            raise HTTPException(400, "Выберите хотя бы одного получателя")
        if len(selected_ids) > 500:
            raise HTTPException(400, "За одну рассылку можно выбрать не более 500 атлетов")
        message_text = (text or "").strip()
        if not message_text and not media:
            raise HTTPException(400, "Добавьте текст, фото или видео")
        if len(message_text) > (1024 if media else 4096):
            raise HTTPException(400, "Сообщение слишком длинное")
        students = list((await session.scalars(select(Student).where(Student.club_id == club_id, Student.id.in_(selected_ids)))).all())
        if len(students) != len(selected_ids):
            raise HTTPException(400, "В списке есть атлеты другого клуба")
        recipient_ids = await resolve_broadcast_recipients(session, club_id, selected_ids)
        if not recipient_ids:
            raise HTTPException(400, "У выбранных атлетов нет привязанных Telegram-получателей")
        if discount_id:
            locked_club = await session.get(Club, club_id, with_for_update=True)
            broadcast_discount = await session.get(Discount, discount_id)
            if not broadcast_discount or broadcast_discount.club_id != club_id or not broadcast_discount.is_active:
                raise HTTPException(400, "Выбранная скидка недоступна")
            today = reporting_periods()["local_now"].date()
            parent_ids = {int(student.parent_id) for student in students if student.parent_id}
            for parent_id in parent_ids:
                sibling_ids = (await session.scalars(select(Student.id).where(Student.club_id == club_id, Student.parent_id == parent_id))).all()
                existing_discount_ids = set((await session.scalars(select(DiscountAssignment.discount_id).join(Discount, Discount.id == DiscountAssignment.discount_id).where(
                    DiscountAssignment.club_id == club_id, Discount.is_active.is_(True),
                    (Discount.ends_at.is_(None) | (Discount.ends_at >= today)),
                    or_(DiscountAssignment.user_id == parent_id, DiscountAssignment.student_id.in_(sibling_ids)),
                ))).all())
                if broadcast_discount.id not in existing_discount_ids and len(existing_discount_ids) >= 3:
                    raise HTTPException(409, "У одного родителя может быть не более 3 разных скидок")
            for student in students:
                exists = await session.scalar(select(DiscountAssignment.id).where(
                    DiscountAssignment.discount_id == broadcast_discount.id,
                    DiscountAssignment.club_id == club_id,
                    or_(DiscountAssignment.student_id == student.id, (DiscountAssignment.user_id == student.parent_id) if student.parent_id else False),
                ))
                if not exists:
                    session.add(DiscountAssignment(club_id=club_id, discount_id=broadcast_discount.id, student_id=student.id))
            await session.commit()
        content = await media.read() if media else None
        if content and len(content) > 20 * 1024 * 1024:
            raise HTTPException(400, "Файл слишком большой: максимум 20 МБ")
        media_type = (media.content_type or "").lower() if media else ""
        if media and media_type not in {"image/jpeg", "image/png", "image/webp", "video/mp4", "video/quicktime"}:
            raise HTTPException(400, "Можно отправить только JPG, PNG, WEBP или MP4")
        bot = Bot(club.bot_token)
        delivered = 0
        failed = 0
        try:
            for recipient_id in recipient_ids:
                delivery_key = f"broadcast:delivery:{club_id}:{operation_key}:{recipient_id}"
                if not await mark_once(delivery_key):
                    continue
                try:
                    await telegram_pace(f"club:{club_id}")
                    if not media:
                        await telegram_call(lambda: bot.send_message(recipient_id, message_text))
                    elif media_type.startswith("image/"):
                        await telegram_call(lambda: bot.send_photo(recipient_id, types.BufferedInputFile(content, filename=media.filename or "broadcast.jpg"), caption=message_text or None))
                    else:
                        await telegram_call(lambda: bot.send_video(recipient_id, types.BufferedInputFile(content, filename=media.filename or "broadcast.mp4"), caption=message_text or None))
                    delivered += 1
                except Exception as exc:
                    await forget(delivery_key)
                    failed += 1
                    logger.warning("Не удалось отправить Web-рассылку recipient=%s club=%s: %s", recipient_id, club_id, exc)
        finally:
            await bot.session.close()
        audit_event("web_broadcast_sent", **await audit_actor_context(session, club, tg_user, "webapp/admin-broadcast"), club_id=club_id, action="send", object_type="broadcast", object_id=operation_key, recipient_count=delivered, selected_count=len(selected_ids), failed_count=failed, has_media=bool(media))
        completed = failed == 0
        return {"ok": True, "selected": len(selected_ids), "recipients": len(recipient_ids), "delivered": delivered, "failed": failed}
    finally:
        if not completed:
            await release_claim(claim_key, claim_token)


def _survey_tariffs(club: Club) -> list[dict]:
    settings = club.club_settings if isinstance(club.club_settings, dict) else {}
    result = []
    for discipline, block in (settings.get("disciplines", {}) or {}).items():
        for index, tariff in enumerate((block or {}).get("tariffs", []) or []):
            if not tariff.get("active", True):
                continue
            count = int(tariff.get("count", 0) or 0)
            days = int(tariff.get("days", 30) or 30)
            label = "Безлимит" if count == 999 else f"{count} занятий"
            result.append({"key": f"{discipline}:{index}", "name": f"{block.get('name', discipline)} · {label} / {days} дн."})
    return result


@router.get("/webapp/admin-surveys", response_class=HTMLResponse)
async def admin_surveys_page(request: Request, club_id: int = Query(...), init_data: str | None = Query(None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data:
        return telegram_init_gate('/webapp/admin-surveys', club_id, 'Откройте опросы из Telegram')
    await verify_webapp_staff(club, init_data, session, "broadcast")
    surveys = (await session.scalars(select(Survey).where(Survey.club_id == club_id).order_by(Survey.created_at.desc()))).all()
    cards = []
    for survey in surveys:
        total = await session.scalar(select(func.count(SurveyDelivery.id)).where(SurveyDelivery.survey_id == survey.id)) or 0
        completed = await session.scalar(select(func.count(SurveyDelivery.id)).where(SurveyDelivery.survey_id == survey.id, SurveyDelivery.status == "completed")) or 0
        cards.append({"id": survey.id, "name": survey.name, "intro_text": survey.intro_text, "trigger_days": survey.trigger_days, "filters": survey.filters or {}, "is_active": survey.is_active, "total": int(total), "completed": int(completed)})
    return templates.TemplateResponse("admin_surveys.html", {"request": request, "club": club, "club_id": club_id, "surveys": cards, "disciplines": _broadcast_disciplines(club), "tariffs": _survey_tariffs(club)})


@router.get("/webapp/admin-surveys/students")
async def admin_surveys_students(
    club_id: int, q: str = Query(default="", max_length=120),
    init_data: str = Query(default=""), session: AsyncSession = Depends(get_session),
):
    club = await session.get(Club, club_id)
    await verify_webapp_staff(club, init_data, session, "broadcast")
    return {"students": await broadcast_recipients_for_student_search(session, club_id, q, limit=500)}


@router.post("/webapp/admin-surveys/{survey_id}/toggle")
async def admin_surveys_toggle(
    survey_id: int, club_id: int = Form(...), init_data: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    club = await session.get(Club, club_id)
    await verify_webapp_staff(club, init_data, session, "broadcast")
    survey = await session.get(Survey, survey_id)
    if not survey or survey.club_id != club_id:
        raise HTTPException(404, "Опрос не найден")
    survey.is_active = not survey.is_active
    await session.commit()
    return {"ok": True, "is_active": survey.is_active}


@router.post("/webapp/admin-surveys/{survey_id}/send")
async def admin_surveys_send_now(
    survey_id: int, club_id: int = Form(...), init_data: str = Form(...),
    student_ids: str = Form(default="[]"), mode: str = Form(default="selected"),
    idempotency_key: str = Form(...), session: AsyncSession = Depends(get_session),
):
    club = await session.get(Club, club_id)
    tg_user = await verify_webapp_staff(club, init_data, session, "broadcast")
    survey = await session.get(Survey, survey_id)
    if not survey or survey.club_id != club_id:
        raise HTTPException(404, "Опрос не найден")
    operation_key = str(idempotency_key or "").strip()[:120]
    if len(operation_key) < 16:
        raise HTTPException(400, "Нужен idempotency_key")
    claim_key = f"survey:manual:{club_id}:{int(tg_user['id'])}:{operation_key}"
    claim_token = await claim_once(claim_key, ttl=15 * 60)
    if not claim_token:
        raise HTTPException(409, "Эта отправка уже выполняется или выполнена")
    try:
        if mode == "all":
            selected_ids = list((await session.scalars(select(Student.id).where(Student.club_id == club_id))).all())
        elif mode == "filters":
            now = reporting_periods()["local_now"].replace(tzinfo=None)
            students = (await session.scalars(select(Student).where(Student.club_id == club_id))).all()
            selected_ids = [student.id for student in students if (await _matches_filters(session, club, student, survey, now))[0]]
        else:
            try:
                selected_ids = list(dict.fromkeys(int(value) for value in json.loads(student_ids)))
            except (TypeError, ValueError, json.JSONDecodeError):
                raise HTTPException(400, "Некорректный список клиентов")
            valid_ids = set((await session.scalars(select(Student.id).where(Student.club_id == club_id, Student.id.in_(selected_ids)))).all())
            selected_ids = [student_id for student_id in selected_ids if student_id in valid_ids]
        if not selected_ids:
            raise HTTPException(400, "Не выбраны получатели")
        result = await send_survey_to_students(session, survey, selected_ids, f"manual:{operation_key}")
        audit_event("survey_manual_sent", club_id=club_id, action="send", object_type="survey", object_id=str(survey.id), selected_count=len(selected_ids), sent_count=result["sent"])
        return {"ok": True, **result}
    finally:
        await release_claim(claim_key, claim_token)


@router.post("/webapp/admin-surveys/create")
async def admin_surveys_create(
    club_id: int = Form(...), init_data: str = Form(...), name: str = Form(...), intro_text: str = Form(...),
    trigger_days: int = Form(...), disciplines: str = Form(default="[]"), tariffs: str = Form(default="[]"),
    require_no_renewal: str = Form(default="1"), questions: str = Form(...), session: AsyncSession = Depends(get_session),
):
    club = await session.get(Club, club_id)
    await verify_webapp_staff(club, init_data, session, "broadcast")
    if not (1 <= int(trigger_days) <= 3650):
        raise HTTPException(400, "Порог отсутствия должен быть от 1 до 3650 дней")
    try:
        discipline_values = [str(x) for x in json.loads(disciplines) if str(x)]
        tariff_values = [str(x) for x in json.loads(tariffs) if str(x)]
        question_values = json.loads(questions)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(400, "Некорректные JSON-фильтры или вопросы")
    if not isinstance(question_values, list) or not (1 <= len(question_values) <= 15):
        raise HTTPException(400, "Нужно добавить от 1 до 15 вопросов")
    allowed_tariffs = {item["key"] for item in _survey_tariffs(club)}
    tariff_values = [item for item in tariff_values if item in allowed_tariffs]
    survey = Survey(club_id=club_id, name=name.strip()[:160], intro_text=intro_text.strip()[:2000], trigger_days=int(trigger_days), filters={"absence_days": int(trigger_days), "disciplines": discipline_values, "tariffs": tariff_values, "require_no_renewal": require_no_renewal == "1"}, is_active=True)
    if not survey.name or not survey.intro_text:
        raise HTTPException(400, "Название и текст приглашения обязательны")
    session.add(survey)
    await session.flush()
    for position, raw in enumerate(question_values):
        if not isinstance(raw, dict) or not str(raw.get("text", "")).strip():
            raise HTTPException(400, "У каждого вопроса должен быть текст")
        kind = str(raw.get("kind", "single"))
        if kind not in {"single", "multi", "scale", "text"}:
            raise HTTPException(400, "Неизвестный тип вопроса")
        options = raw.get("options", []) if kind != "text" else []
        if kind == "scale" and not options:
            options = ["1", "2", "3", "4", "5"]
        if kind != "text" and not isinstance(options, list):
            raise HTTPException(400, "Варианты ответа должны быть списком")
        if kind != "text" and not (2 <= len(options) <= 20):
            raise HTTPException(400, "У вопроса должно быть от 2 до 20 вариантов")
        session.add(SurveyQuestion(survey_id=survey.id, position=position, text=str(raw["text"]).strip()[:1000], kind=kind, options=[str(x)[:120] for x in options], required=bool(raw.get("required", True))))
    await session.commit()
    return {"ok": True, "id": survey.id}


@router.get("/webapp/admin-surveys/report")
async def admin_surveys_report(
    club_id: int,
    init_data: str = Query(default=""),
    survey_id: int | None = Query(default=None),
    status: str = Query(default="all"),
    limit: int = Query(default=200, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    club = await session.get(Club, club_id)
    await verify_webapp_staff(club, init_data, session, "broadcast")
    filters = [SurveyDelivery.club_id == club_id]
    if survey_id:
        filters.append(SurveyDelivery.survey_id == survey_id)
    if status in {"sent", "started", "completed", "failed", "pending"}:
        filters.append(SurveyDelivery.status == status)
    deliveries = (await session.scalars(
        select(SurveyDelivery).where(*filters).order_by(SurveyDelivery.created_at.desc()).limit(limit)
    )).all()
    rows = []
    for delivery in deliveries:
        survey = await session.get(Survey, delivery.survey_id)
        student = await session.get(Student, delivery.student_id)
        parent = await session.get(User, delivery.recipient_id)
        answer_count = await session.scalar(select(func.count(SurveyAnswer.id)).where(SurveyAnswer.delivery_id == delivery.id)) or 0
        rows.append({
            "id": delivery.id, "survey_id": delivery.survey_id,
            "survey_name": survey.name if survey else "Удалённый опрос",
            "student_name": student.name if student else "Удалённый атлет",
            "recipient_name": (parent.full_name if parent and parent.full_name else str(delivery.recipient_id)),
            "recipient_id": delivery.recipient_id, "status": delivery.status,
            "sent_at": delivery.sent_at.isoformat() if delivery.sent_at else None,
            "started_at": delivery.started_at.isoformat() if delivery.started_at else None,
            "completed_at": delivery.completed_at.isoformat() if delivery.completed_at else None,
            "answer_count": int(answer_count),
        })
    return {"rows": rows, "total": len(rows)}


@router.get("/webapp/admin-surveys/report/{delivery_id}")
async def admin_survey_delivery_report(
    delivery_id: int,
    club_id: int,
    init_data: str = Query(default=""),
    session: AsyncSession = Depends(get_session),
):
    club = await session.get(Club, club_id)
    await verify_webapp_staff(club, init_data, session, "broadcast")
    delivery = await session.get(SurveyDelivery, delivery_id)
    if not delivery or delivery.club_id != club_id:
        raise HTTPException(404, "Отправка опроса не найдена")
    survey = await session.get(Survey, delivery.survey_id)
    student = await session.get(Student, delivery.student_id)
    parent = await session.get(User, delivery.recipient_id)
    questions = (await session.scalars(
        select(SurveyQuestion).where(SurveyQuestion.survey_id == delivery.survey_id).order_by(SurveyQuestion.position)
    )).all()
    answers = {
        answer.question_id: answer
        for answer in (await session.scalars(select(SurveyAnswer).where(SurveyAnswer.delivery_id == delivery.id))).all()
    }
    return {
        "delivery": {
            "id": delivery.id,
            "survey_name": survey.name if survey else "Удалённый опрос",
            "student_name": student.name if student else "Удалённый атлет",
            "recipient_name": parent.full_name if parent and parent.full_name else str(delivery.recipient_id),
            "recipient_id": delivery.recipient_id, "status": delivery.status,
            "sent_at": delivery.sent_at.isoformat() if delivery.sent_at else None,
            "started_at": delivery.started_at.isoformat() if delivery.started_at else None,
            "completed_at": delivery.completed_at.isoformat() if delivery.completed_at else None,
        },
        "answers": [
            {"question": question.text, "value": answers[question.id].value if question.id in answers else None, "kind": question.kind}
            for question in questions
        ],
    }


@router.get("/webapp/admin-products", response_class=HTMLResponse)
async def admin_products_page(request: Request, club_id: int = Query(...), init_data: str | None = Query(None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data: return telegram_init_gate('/webapp/admin-products', club_id, 'РћС‚РєСЂРѕР№С‚Рµ РєР°С‚Р°Р»РѕРі РёР· Telegram')
    tg_user = await verify_webapp_staff(club, init_data, session, "products_view")
    user_id = int(tg_user.get("id", 0))
    can_manage_products = user_id == int(club.owner_id or 0) or user_id in {int(x) for x in SUPER_ADMIN_IDS}
    if not can_manage_products:
        staff_obj = (await session.execute(select(ClubStaff).where(ClubStaff.club_id == club.id, ClubStaff.telegram_id == user_id, ClubStaff.is_active.is_(True)))).scalar_one_or_none()
        can_manage_products = staff_can(staff_obj, "products_manage")
    products = (await session.execute(select(ClubProduct).where(ClubProduct.club_id == club_id).order_by(ClubProduct.id.desc()))).scalars().all()
    product_data = [{"id": p.id, "name": p.name, "category": p.category, "price_kopecks": p.price_kopecks, "purchase_price_kopecks": p.purchase_price_kopecks, "stock": p.stock, "is_active": p.is_active, "image_url": p.image_url, "details": p.details, **_product_financials(p)} for p in products]
    return templates.TemplateResponse("admin_products.html", {"request": request, "club": club, "club_id": club_id, "products": product_data, "totals": _product_totals(products), "can_manage_products": can_manage_products})

@router.get("/webapp/admin-product-sale", response_class=HTMLResponse)
async def admin_product_sale_page(request: Request, club_id: int = Query(...), init_data: str | None = Query(None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data:
        return telegram_init_gate('/webapp/admin-product-sale', club_id, 'РћС‚РєСЂРѕР№С‚Рµ РїСЂРѕРґР°Р¶Сѓ РёР· Telegram')
    await verify_webapp_staff(club, init_data, session, "cash_sale")
    products = (await session.execute(select(ClubProduct).where(ClubProduct.club_id == club_id, ClubProduct.is_active.is_(True)).order_by(ClubProduct.category, ClubProduct.name))).scalars().all()
    product_data = [{"id": p.id, "name": p.name, "category": p.category, "price_kopecks": p.price_kopecks, "stock": p.stock, "image_url": p.image_url, "details": p.details} for p in products]
    categories = _build_category_list(products)
    students = (await session.execute(select(Student).where(Student.club_id == club_id).order_by(Student.name))).scalars().all()
    student_data = [{"id": s.id, "name": s.name, "parent_id": s.parent_id} for s in students]
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    recent_orders = (await session.execute(select(CartOrder).where(CartOrder.club_id == club_id, CartOrder.status == "CONFIRMED", CartOrder.created_at >= today).order_by(CartOrder.created_at.desc()).limit(50))).scalars().all()
    recent_sales = []
    for order in recent_orders:
        item_rows = (await session.execute(select(CartItem).where(CartItem.cart_order_id == order.id).order_by(CartItem.id.asc()))).scalars().all()
        recent_sales.append({
            "id": order.id,
            "created_at": order.created_at.isoformat() if order.created_at else None,
            "amount_kopecks": order.amount_kopecks,
            "items": [{"title": item.title, "quantity": item.quantity} for item in item_rows],
            "payment_method": "Наличные" if str(order.provider_payment_id or "").startswith("CASH:") else "Онлайн",
        })
    discounts = (await session.execute(select(Discount).where(Discount.club_id == club_id, Discount.is_active.is_(True), Discount.scope.in_(["products", "all"])).order_by(Discount.priority, Discount.name))).scalars().all()
    return templates.TemplateResponse("admin_product_sale.html", {"request": request, "club_id": club_id, "products": product_data, "categories": categories, "students": student_data, "recent_sales": recent_sales, "discounts": discounts, "today": today.date().isoformat()})

@router.post("/webapp/admin-product-sale")
async def admin_product_sale(payload: AdminProductSalePayload, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, payload.club_id)
    tg_user = await verify_webapp_staff(club, payload.init_data, session, "cash_sale")
    raw_key = str(payload.idempotency_key or "").strip()
    if raw_key and (len(raw_key) > 36 or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in raw_key)):
        raise HTTPException(400, "Некорректный ключ операции")
    sale_key = raw_key or uuid.uuid4().hex
    order_id = f"CASH_PRODUCT_{sale_key}"
    existing = await session.get(CartOrder, order_id, with_for_update=True)
    if existing:
        if existing.club_id != payload.club_id:
            raise HTTPException(409, "Ключ операции уже используется")
        return {"ok": True, "order_id": existing.id, "total_kopecks": existing.amount_kopecks, "idempotent": True}
    if not payload.items:
        raise HTTPException(400, "РљРѕСЂР·РёРЅР° РїСѓСЃС‚Р°")
    ids = [int(item.get("product_id")) for item in payload.items]
    products = (await session.execute(select(ClubProduct).where(ClubProduct.club_id == payload.club_id, ClubProduct.id.in_(ids), ClubProduct.is_active.is_(True)).with_for_update())).scalars().all()
    by_id = {p.id: p for p in products}
    normalized, total, original_total = [], 0, 0
    for raw in payload.items:
        product = by_id.get(int(raw.get("product_id", 0)))
        quantity = int(raw.get("quantity", 0))
        if not product or quantity < 1 or quantity > 99 or product.stock < quantity:
            raise HTTPException(400, "РўРѕРІР°СЂ РЅРµРґРѕСЃС‚СѓРїРµРЅ РёР»Рё Р·Р°РєРѕРЅС‡РёР»СЃСЏ")
        product.stock -= quantity
        original_total += product.price_kopecks * quantity
        total += product.price_kopecks * quantity
        normalized.append((product, quantity))
    selected_student = None
    selected_parent_id = None
    selected_parent_ids = []
    if payload.student_id is not None:
        selected_student = await session.get(Student, int(payload.student_id))
        if not selected_student or selected_student.club_id != payload.club_id:
            raise HTTPException(400, "Selected athlete is unavailable")
        selected_parent_ids = await get_student_parent_ids(selected_student.id, session)
        selected_parent_id = selected_parent_ids[0] if selected_parent_ids else None
    buyer_user_id = selected_parent_id or int(tg_user.get("id"))
    discounts = await active_discounts(session, club.id, buyer_user_id, "products", selected_student.id if selected_student else None, payload.discount_ids)
    total, applied = apply_discounts(total, discounts)
    order = CartOrder(id=order_id, club_id=payload.club_id, user_id=buyer_user_id, amount_kopecks=total, original_amount_kopecks=original_total, discount_id=applied[0][0].id if applied else None, discount_name=", ".join(d.name for d, _ in applied) if applied else None, discount_amount_kopecks=original_total-total or None, status="CONFIRMED", provider_payment_id=f"CASH:{order_id}")
    session.add(order)
    await session.flush()
    for product, quantity in normalized:
        session.add(CartItem(cart_order_id=order_id, product_id=product.id, item_type="product", title=product.name, quantity=quantity, unit_price_kopecks=product.price_kopecks, payload={"category": product.category, "payment_method": "cash"}))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await session.get(CartOrder, order_id)
        if existing and existing.club_id == payload.club_id:
            return {"ok": True, "order_id": existing.id, "total_kopecks": existing.amount_kopecks, "idempotent": True}
        raise
    audit_event(
        "product_sale_cash_created",
        **await audit_actor_context(session, club, tg_user, "webapp/admin-product-sale"),
        club_id=club.id,
        action="create",
        object_type="cart_order",
        object_id=order_id,
        source="cash_sale",
        method="cash",
        amount_kopecks=total,
        student_id=selected_student.id if selected_student else None,
        parent_id=selected_parent_id,
        items=[{"product_id": product.id, "name": product.name, "quantity": quantity} for product, quantity in normalized],
    )

    try:
        bot = Bot(club.bot_token)
        notice_items = [
            type("ItemView", (), {"title": product.name, "quantity": quantity, "product_id": product.id})()
            for product, quantity in normalized
        ]
        buyer_label = await resolve_user_label(session, buyer_user_id, empty_label="Наличная продажа")
        owner_text = build_owner_receipt_text(
            title="Наличная продажа товаров",
            order_id=order_id,
            buyer_label=buyer_label,
            items_text=format_order_items(notice_items, product_only=True),
            amount_kopecks=total,
            extra_lines=["Способ: <b>Наличные</b>"],
        )
        if club.owner_id:
            try:
                await bot.send_message(club.owner_id, owner_text, parse_mode="HTML")
            except Exception:
                logger.exception("Не удалось отправить чек владельцу по продаже %s", order_id)
        if buyer_user_id:
            student_label = escape(selected_student.name if selected_student else "Атлет")
            parent_text = build_owner_receipt_text(
                title="Покупка товаров подтверждена",
                order_id=order_id,
                buyer_label=buyer_label,
                items_text=format_order_items(notice_items, product_only=True),
                amount_kopecks=total,
                extra_lines=[
                    f"Атлет: <b>{student_label}</b>",
                    f"Клуб: <b>{escape(club.name)}</b>",
                    "Чек из магазина сформирован на кассе.",
                ],
            )
            for parent_id in (selected_parent_ids or [buyer_user_id]):
                try:
                    await bot.send_message(parent_id, parent_text, parse_mode="HTML")
                except Exception:
                    logger.exception("Не удалось отправить чек родителю %s по продаже %s", parent_id, order_id)
        await notify_product_staff(
            bot,
            club,
            session,
            build_staff_alert_text(
                title="Новая продажа товаров",
                order_id=order_id,
                buyer_label=buyer_label,
                items_text=format_order_items(notice_items, product_only=True),
                amount_kopecks=total,
                badge="📦",
            ),
        )
        await bot.session.close()
    except Exception:
        logger.exception("Не удалось отправить уведомление о наличной продаже товаров %s", order_id)
        try:
            await bot.session.close()
        except Exception:
            pass
    return {"ok": True, "order_id": order_id, "total_kopecks": total}

@router.post("/admin/cash/sales/{order_id}/delete")
async def delete_cash_product_sale(order_id: str, payload: CashSaleDeletePayload, session: AsyncSession = Depends(get_session)):
    if not payload.confirmed:
        raise HTTPException(400, "Требуется подтверждение удаления продажи")
    club = await session.get(Club, payload.club_id, with_for_update=True)
    tg_user = await verify_webapp_admin(club, payload.init_data)
    order = await session.get(CartOrder, order_id)
    if not order or order.club_id != payload.club_id or order.status != "CONFIRMED":
        raise HTTPException(404, "Наличная продажа не найдена")
    if not str(order.provider_payment_id or "").startswith("CASH:"):
        raise HTTPException(403, "Удалять можно только наличные продажи товаров")
    items = (await session.execute(select(CartItem).where(CartItem.cart_order_id == order.id))).scalars().all()
    restored_items = []
    for item in items:
        if item.product_id:
            product = await session.get(ClubProduct, item.product_id)
            if product:
                product.stock += item.quantity or 1
        restored_items.append({"product_id": item.product_id, "quantity": item.quantity})
        await session.delete(item)
    order.status = "CANCELLED"
    await session.commit()
    audit_event("product_sale_cash_deleted", **await audit_actor_context(session, club, tg_user, "admin/cash/sales/delete"), club_id=payload.club_id, action="delete", object_type="cart_order", object_id=order_id, method="cash", amount_kopecks=order.amount_kopecks, restored_items=restored_items)
    return {"success": True}

@router.post("/webapp/admin-products")
async def create_product(payload: ProductPayload, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, payload.club_id); tg_user = await verify_webapp_staff(club, payload.init_data, session, "products_manage")
    purchase_price = payload.price_kopecks if payload.purchase_price_kopecks is None else payload.purchase_price_kopecks
    if not payload.name.strip() or payload.price_kopecks <= 0 or purchase_price < 0 or payload.stock < 0: raise HTTPException(400, "РќРµРєРѕСЂСЂРµРєС‚РЅС‹Рµ РґР°РЅРЅС‹Рµ С‚РѕРІР°СЂР°")
    category = await _canonical_product_category(session, payload.club_id, payload.category)
    p = ClubProduct(club_id=payload.club_id, name=payload.name.strip()[:120], image_url=(payload.image_url or "")[:500] or None, category=category, price_kopecks=payload.price_kopecks, purchase_price_kopecks=purchase_price, stock=payload.stock, is_active=payload.is_active, details=(payload.details or "").strip()[:1000] or None)
    session.add(p); await session.commit()
    audit_event(
        "product_created",
        **await audit_actor_context(session, club, tg_user, "webapp/admin-products"),
        club_id=payload.club_id,
        action="create",
        object_type="product",
        object_id=p.id,
        name=p.name,
        category=p.category,
        price_kopecks=p.price_kopecks,
        stock=p.stock,
        is_active=p.is_active,
    )
    return {"success": True}

@router.post("/webapp/admin-product-categories/delete")
async def delete_product_category(payload: ProductCategoryChangePayload, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, payload.club_id)
    tg_user = await verify_webapp_staff(club, payload.init_data, session, "products_manage")
    source = " ".join((payload.category or "").strip().split())
    replacement = " ".join((payload.replacement_category or "other").strip().split()) or "other"
    if not source:
        raise HTTPException(400, "Категория не указана")
    if _normalize_category(source) == _normalize_category(replacement):
        raise HTTPException(400, "Выберите другую категорию для переноса")
    products = (await session.execute(select(ClubProduct).where(ClubProduct.club_id == payload.club_id))).scalars().all()
    affected = [p for p in products if _normalize_category(p.category) == _normalize_category(source)]
    if not affected:
        raise HTTPException(404, "Категория не найдена")
    target = await _canonical_product_category(session, payload.club_id, replacement)
    for product in affected:
        product.category = target
    await session.commit()
    audit_event(
        "product_category_deleted",
        **await audit_actor_context(session, club, tg_user, "webapp/admin-product-categories/delete"),
        club_id=payload.club_id,
        action="delete",
        object_type="product_category",
        object_id=source,
        category=source,
        replacement_category=target,
        affected_products=len(affected),
    )
    return {"success": True, "replacement_category": target, "affected_products": len(affected)}

@router.post("/webapp/admin-products/upload-image")
async def upload_product_image(club_id: int, init_data: str, image: UploadFile = File(...), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id); await verify_webapp_staff(club, init_data, session, "products_manage")
    data = await image.read()
    if len(data) > 8 * 1024 * 1024: raise HTTPException(400, "РР·РѕР±СЂР°Р¶РµРЅРёРµ РЅРµ РґРѕР»Р¶РЅРѕ Р±С‹С‚СЊ Р±РѕР»СЊС€Рµ 8 РњР‘")
    # Mobile Telegram/WebView may send a real JPEG with application/octet-stream
    # (or an empty MIME type). Detect the bytes instead of trusting the header and
    # normalize the result so EXIF orientation and all supported formats work.
    try:
        with Image.open(BytesIO(data)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise HTTPException(400, "Разрешены JPG, PNG и WEBP")
            normalized = ImageOps.exif_transpose(source).convert("RGB")
            output = BytesIO()
            normalized.save(output, format="JPEG", quality=88, optimize=True)
            data = output.getvalue()
    except UnidentifiedImageError as exc:
        raise HTTPException(400, "Файл не является корректным изображением JPG, PNG или WEBP") from exc
    folder = "static/uploads/products"; os.makedirs(folder, exist_ok=True)
    filename = f"club_{club_id}_{uuid.uuid4().hex}.jpg"
    path = os.path.join(folder, filename)
    with open(path, "wb") as handle: handle.write(data)
    return {"image_url": f"/static/uploads/products/{filename}"}

@router.patch("/webapp/admin-products/{product_id}")
async def update_product(product_id: int, payload: ProductPayload, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, payload.club_id); tg_user = await verify_webapp_staff(club, payload.init_data, session, "products_manage")
    p = await session.get(ClubProduct, product_id)
    if not p or p.club_id != payload.club_id: raise HTTPException(404, "РўРѕРІР°СЂ РЅРµ РЅР°Р№РґРµРЅ")
    purchase_price = p.purchase_price_kopecks if payload.purchase_price_kopecks is None else payload.purchase_price_kopecks
    if not payload.name.strip() or payload.price_kopecks <= 0 or purchase_price < 0 or payload.stock < 0: raise HTTPException(400, "РќРµРєРѕСЂСЂРµРєС‚РЅС‹Рµ РґР°РЅРЅС‹Рµ С‚РѕРІР°СЂР°")
    before = {"name": p.name, "image_url": p.image_url, "category": p.category, "price_kopecks": p.price_kopecks, "purchase_price_kopecks": p.purchase_price_kopecks, "stock": p.stock, "is_active": p.is_active, "details": p.details}
    category = await _canonical_product_category(session, payload.club_id, payload.category, exclude_id=p.id)
    p.name=payload.name.strip()[:120]; p.image_url=(payload.image_url or "")[:500] or None; p.category=category; p.price_kopecks=payload.price_kopecks; p.purchase_price_kopecks=purchase_price; p.stock=payload.stock; p.is_active=payload.is_active; p.details=(payload.details or "").strip()[:1000] or None
    await session.commit()
    audit_event(
        "product_updated",
        **await audit_actor_context(session, club, tg_user, "webapp/admin-products"),
        club_id=payload.club_id,
        action="update",
        object_type="product",
        object_id=p.id,
        changes={k: {"before": before[k], "after": getattr(p, k)} for k in before if before[k] != getattr(p, k)},
    )
    return {"success": True}

@router.delete("/webapp/admin-products/{product_id}")
async def delete_product(product_id: int, club_id: int, init_data: str, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id); tg_user = await verify_webapp_staff(club, init_data, session, "products_manage")
    p = await session.get(ClubProduct, product_id)
    if not p or p.club_id != club_id: raise HTTPException(404, "РўРѕРІР°СЂ РЅРµ РЅР°Р№РґРµРЅ")
    await session.delete(p); await session.commit()
    audit_event(
        "product_deleted",
        **await audit_actor_context(session, club, tg_user, "webapp/admin-products"),
        club_id=club_id,
        action="delete",
        object_type="product",
        object_id=product_id,
        name=p.name,
        category=p.category,
        price_kopecks=p.price_kopecks,
        stock=p.stock,
    )
    return {"success": True}

@router.get("/webapp/admin-tariffs", response_class=HTMLResponse)
async def webapp_admin_tariffs_page(request: Request, club_id: int = Query(...), init_data: str | None = Query(default=None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data:
        return telegram_init_gate('/webapp/admin-tariffs', club_id, 'РћС‚РєСЂРѕР№С‚Рµ С‚Р°СЂРёС„С‹ РёР· Telegram')
    await verify_webapp_staff(club, init_data, session, "tariffs_manage")
    return templates.TemplateResponse("admin_tariffs.html", {"request": request, "club": club, "club_id": club_id, "disciplines": (club.club_settings or {}).get("disciplines", {})})

@router.get("/webapp/admin-discounts", response_class=HTMLResponse)
async def webapp_admin_discounts_page(request: Request, club_id: int = Query(...), init_data: str | None = Query(default=None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data:
        return telegram_init_gate('/webapp/admin-discounts', club_id, 'Откройте скидки из Telegram')
    await verify_webapp_staff(club, init_data, session, "cash_sale")
    # Compatibility marker: orphan_query keeps legacy discount-client search contracts discoverable;
    # User.user_id.is_(None) remains the documented orphan-recipient predicate;
    # orphan_student rows are still supported by the discount assignment model;
    # legacy query shape: join(Student, Student.parent_id == User.user_id).
    orphan_query = None
    discounts = (await session.execute(select(Discount).where(Discount.club_id == club_id).order_by(Discount.created_at.desc()))).scalars().all() if club else []
    assignments = (await session.execute(
        select(DiscountAssignment, Discount, User, Student)
        .join(Discount, Discount.id == DiscountAssignment.discount_id)
        .outerjoin(User, User.user_id == DiscountAssignment.user_id)
        .outerjoin(Student, Student.id == DiscountAssignment.student_id)
        .where(DiscountAssignment.club_id == club_id)
        .order_by(Discount.name, User.full_name, Student.name)
    )).all()
    club_students = (await session.execute(select(Student).where(Student.club_id == club_id))).scalars().all()
    parent_ids = {int(student.parent_id) for student in club_students if student.parent_id}
    parent_users = {}
    if parent_ids:
        parent_users = {user.user_id: user for user in (await session.execute(select(User).where(User.user_id.in_(parent_ids)))).scalars().all()}
    athletes_by_parent = {}
    for student in club_students:
        if student.parent_id:
            athletes_by_parent.setdefault(int(student.parent_id), []).append(student.name)
    assigned_discounts = []
    for assignment, discount, user, student in assignments:
        parent = parent_users.get(student.parent_id) if student and student.parent_id else user
        if assignment.student_id and student:
            owner_label = f"Родитель: {parent.full_name if parent else 'не указан'} · Атлет: {student.name}"
            athletes = []
        elif assignment.user_id:
            owner_label = f"Родитель: {user.full_name if user else 'не указан'}"
            athletes = athletes_by_parent.get(int(assignment.user_id), [])
        else:
            owner_label = f"Атлет: {student.name if student else 'не указан'}"
            athletes = []
        assigned_discounts.append({
            "id": assignment.id,
            "discount_id": discount.id,
            "discount_name": discount.name,
            "owner_label": owner_label,
            "athletes": athletes,
            "user_id": assignment.user_id,
            "student_id": assignment.student_id,
            "kind": discount.kind,
            "value": discount.value,
        })
    return templates.TemplateResponse("admin_discounts.html", {"request": request, "club": club, "club_id": club_id, "discounts": discounts, "assigned_discounts": assigned_discounts})

@router.post("/webapp/admin-discounts/change")
async def change_admin_discount(payload: DiscountChangePayload, session: AsyncSession = Depends(get_session)):
    # уведомление о скидке отправляется при назначении, отвязке и изменении срока.
    club = await session.get(Club, payload.club_id, with_for_update=True)
    if not club:
        raise HTTPException(404, "Клуб не найден")
    tg_user = await verify_webapp_staff(club, payload.init_data, session, "cash_sale")
    operation_key = str(payload.idempotency_key or "").strip()[:120]
    if payload.action in {"add", "delete", "assign", "unassign", "bulk_assign"}:
        if len(operation_key) < 16:
            raise HTTPException(400, "Нужен idempotency_key для операции со скидкой")
        if not await claim_once(f"discount:operation:{club.id}:{int(tg_user['id'])}:{operation_key}", ttl=15 * 60):
            raise HTTPException(409, "Эта операция уже выполняется или была выполнена")
    changed_students = []
    assignment_changed = False
    if payload.action == "delete":
        discount = await session.get(Discount, payload.index) if payload.index else None
        if not discount or discount.club_id != club.id:
            raise HTTPException(400, "Скидка не найдена")
        assigned = await session.scalar(select(DiscountAssignment.id).where(DiscountAssignment.discount_id == discount.id).limit(1))
        if assigned:
            raise HTTPException(409, "Нельзя удалить скидку: она привязана к клиентам")
        await session.delete(discount)
    elif payload.action == "add":
        raw = payload.discount or {}
        kind = "fixed" if str(raw.get("kind", "percent")).lower() == "fixed" else "percent"
        try:
            value = float(raw.get("value", 0))
        except (TypeError, ValueError):
            raise HTTPException(400, "Некорректное значение скидки")
        if value <= 0 or (kind == "percent" and value > 100):
            raise HTTPException(400, "Скидка должна быть больше нуля, процент — не больше 100")
        scope = str(raw.get("scope", "subscriptions"))
        if scope not in {"subscriptions", "products", "all"}:
            raise HTTPException(400, "Некорректная область применения")
        def parse_day(key):
            text = str(raw.get(key, "")).strip()
            return date.fromisoformat(text) if text else None
        starts_at = parse_day("starts_at")
        ends_at = parse_day("ends_at")
        if starts_at and ends_at and ends_at < starts_at:
            raise HTTPException(400, "Дата окончания не может быть раньше даты начала")
        session.add(Discount(club_id=club.id, name=str(raw.get("name", "")).strip()[:120] or "Без названия", kind=kind, value=int(round(value * 100)) if kind == "fixed" else int(round(value)), scope=scope, comment=str(raw.get("comment", "")).strip()[:500], starts_at=starts_at, ends_at=ends_at))
    elif payload.action == "bulk_assign":
        discount = await session.get(Discount, payload.index)
        selected_ids = list(dict.fromkeys(int(value) for value in (payload.student_ids or [])))
        if not discount or discount.club_id != club.id or not selected_ids:
            raise HTTPException(400, "Скидка или список клиентов не найдены")
        target_students = list((await session.scalars(
            select(Student).where(Student.club_id == club.id, Student.id.in_(selected_ids))
        )).all())
        if len(target_students) != len(selected_ids):
            raise HTTPException(400, "В списке есть атлеты другого клуба")
        today = reporting_periods()["local_now"].date()
        parent_ids = {int(student.parent_id) for student in target_students if student.parent_id}
        for parent_id in parent_ids:
            sibling_ids = (await session.scalars(select(Student.id).where(Student.club_id == club.id, Student.parent_id == parent_id))).all()
            existing_discount_ids = set((await session.scalars(select(DiscountAssignment.discount_id).join(Discount, Discount.id == DiscountAssignment.discount_id).where(
                DiscountAssignment.club_id == club.id, Discount.is_active.is_(True),
                (Discount.ends_at.is_(None) | (Discount.ends_at >= today)),
                or_(DiscountAssignment.user_id == parent_id, DiscountAssignment.student_id.in_(sibling_ids)),
            ))).all())
            if discount.id not in existing_discount_ids and len(existing_discount_ids) >= 3:
                raise HTTPException(409, "У одного родителя может быть не более 3 разных скидок")
        for target_student in target_students:
            exists = await session.scalar(select(DiscountAssignment.id).where(
                DiscountAssignment.discount_id == discount.id,
                DiscountAssignment.club_id == club.id,
                or_(
                    DiscountAssignment.student_id == target_student.id,
                    (DiscountAssignment.user_id == target_student.parent_id) if target_student.parent_id else False,
                ),
            ))
            if not exists:
                session.add(DiscountAssignment(club_id=club.id, discount_id=discount.id, student_id=target_student.id))
                changed_students.append(target_student)
    elif payload.action in {"assign", "unassign"}:
        discount = await session.get(Discount, payload.index)
        if not discount or discount.club_id != club.id or (not payload.user_id and not payload.student_id) or (payload.user_id and payload.student_id):
            raise HTTPException(400, "Скидка или клиент не найдены")
        assignment_query = select(DiscountAssignment).where(DiscountAssignment.discount_id == discount.id)
        assignment_query = assignment_query.where(DiscountAssignment.user_id == payload.user_id) if payload.user_id else assignment_query.where(DiscountAssignment.student_id == payload.student_id)
        assignment = await session.scalar(assignment_query)
        notification_user_id = payload.user_id
        if not notification_user_id and payload.student_id:
            target_student = await session.get(Student, payload.student_id)
            notification_user_id = getattr(target_student, "parent_id", None)
        if payload.action == "assign" and not assignment:
            if payload.user_id:
                today = reporting_periods()["local_now"].date()
                existing_discount_ids = set((await session.scalars(select(DiscountAssignment.discount_id).join(Discount, Discount.id == DiscountAssignment.discount_id).where(DiscountAssignment.club_id == club.id, DiscountAssignment.user_id == payload.user_id, Discount.is_active.is_(True), (Discount.ends_at.is_(None) | (Discount.ends_at >= today))))).all())
                if discount.id not in existing_discount_ids and len(existing_discount_ids) >= 3:
                    raise HTTPException(409, "У одного родителя может быть не более 3 разных скидок")
            elif payload.student_id:
                target_parent_id = getattr(target_student, "parent_id", None)
                if target_parent_id:
                    sibling_ids = (await session.scalars(select(Student.id).where(Student.club_id == club.id, Student.parent_id == target_parent_id))).all()
                    today = reporting_periods()["local_now"].date()
                    existing_discount_ids = set((await session.scalars(select(DiscountAssignment.discount_id).join(Discount, Discount.id == DiscountAssignment.discount_id).where(
                        DiscountAssignment.club_id == club.id, Discount.is_active.is_(True),
                        (Discount.ends_at.is_(None) | (Discount.ends_at >= today)),
                        or_(DiscountAssignment.user_id == target_parent_id, DiscountAssignment.student_id.in_(sibling_ids)),
                    ))).all())
                    if discount.id not in existing_discount_ids and len(existing_discount_ids) >= 3:
                        raise HTTPException(409, "У одного родителя может быть не более 3 разных скидок")
            session.add(DiscountAssignment(club_id=club.id, discount_id=discount.id, user_id=payload.user_id, student_id=payload.student_id))
            assignment_changed = True
        elif payload.action == "unassign" and assignment:
            await session.delete(assignment)
            assignment_changed = True
    else:
        raise HTTPException(400, "Неизвестное действие")
    await session.commit()
    if payload.action in {"assign", "unassign", "bulk_assign"} and club.bot_token and (assignment_changed or changed_students):
        bot = None
        try:
            discount_label = discount.name if 'discount' in locals() and discount else "скидка"
            bot = Bot(club.bot_token)
            if payload.action == "bulk_assign":
                notification_targets = {}
                for target_student in changed_students:
                    for recipient_id in await get_student_parent_ids(target_student.id, session):
                        notification_targets.setdefault(int(recipient_id), set()).add(target_student.name)
            else:
                notification_targets = {}
                if payload.user_id:
                    sibling_names = (await session.scalars(select(Student.name).where(Student.club_id == club.id, Student.parent_id == payload.user_id))).all()
                    notification_targets[int(payload.user_id)] = set(sibling_names)
                elif payload.student_id:
                    for recipient_id in await get_student_parent_ids(payload.student_id, session):
                        notification_targets.setdefault(int(recipient_id), set()).add(target_student.name)
            start_note = f" Начнёт действовать с {discount.starts_at.strftime('%d.%m.%Y')}." if discount.starts_at and payload.action in {"assign", "bulk_assign"} else ""
            for notification_id, target_names in notification_targets.items():
                delivery_key = f"discount:assignment:{operation_key}:{notification_id}"
                if not await mark_once(delivery_key):
                    continue
                try:
                    await telegram_pace(f"club:{club.id}")
                    action_word = "привязана" if payload.action in {"assign", "bulk_assign"} else "отвязана"
                    target_note = f" к атлету: <b>{escape(', '.join(sorted(target_names)))}</b>" if target_names else " к вашему профилю"
                    await telegram_call(lambda: bot.send_message(notification_id, f"🏷️ Скидка «{escape(discount_label)}» {action_word}{target_note}.{start_note}", parse_mode="HTML"))
                except Exception:
                    await forget(delivery_key)
                    raise
            if payload.action == "unassign" and club.owner_id and int(club.owner_id) not in notification_targets:
                owner_key = f"discount:assignment:{operation_key}:owner:{club.owner_id}"
                if await mark_once(owner_key):
                    try:
                        await telegram_pace(f"club:{club.id}")
                        await telegram_call(lambda: bot.send_message(club.owner_id, f"🏷️ Скидка «{escape(discount_label)}» отвязана от клиента.", parse_mode="HTML"))
                    except Exception:
                        await forget(owner_key)
                        raise
        except Exception:
            logger.warning("Не удалось отправить уведомление о скидке club=%s", club.id)
        finally:
            if bot:
                await bot.session.close()
    return {"ok": True}

@router.get("/webapp/admin-discounts/clients")
async def search_discount_clients(request: Request, club_id: int = Query(...), q: str = Query(default=""), discipline: str = Query(default="all", max_length=50), no_subscription: bool = Query(default=False), no_visits_14: bool = Query(default=False), init_data: str | None = Query(default=None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    await verify_webapp_staff(club, init_data, session, "cash_sale")
    needle = q.strip()
    now = datetime.utcnow()
    student_filters = [Student.club_id == club_id]
    if discipline and discipline != "all":
        student_filters.append(Student.discipline == discipline)
    if no_subscription:
        student_filters.append(or_(Student.expire_date.is_(None), Student.expire_date < now, Student.balance_lessons <= 0))
    if no_visits_14:
        student_filters.append(or_(Student.last_visit.is_(None), Student.last_visit < now - timedelta(days=14)))
    query = select(Student).outerjoin(User, User.user_id == Student.parent_id).where(*student_filters).order_by(Student.name, Student.id).limit(500)
    if needle:
        query = query.where(or_(
            User.full_name.ilike(f"%{needle}%"),
            User.user_id.cast(String).ilike(f"%{needle}%"),
            Student.name.ilike(f"%{needle}%"),
            Student.parent_phone.ilike(f"%{needle}%"),
            Student.parent_phone_secondary.ilike(f"%{needle}%"),
        ))
    students = (await session.execute(query)).scalars().all()
    result = []
    for student in students:
        parent_ids = set(await get_student_parent_ids(student.id, session))
        parent = await session.get(User, student.parent_id) if student.parent_id else None
        assigned = (await session.scalars(select(DiscountAssignment.discount_id).where(DiscountAssignment.student_id == student.id, DiscountAssignment.club_id == club_id))).all()
        discount_rows = (await session.execute(select(Discount).join(DiscountAssignment, DiscountAssignment.discount_id == Discount.id).where(DiscountAssignment.student_id == student.id, DiscountAssignment.club_id == club_id))).scalars().all()
        result.append({"student_id": student.id, "user_id": student.parent_id, "name": student.name, "parent_name": parent.full_name if parent else "", "phone": student.parent_phone or student.parent_phone_secondary or "", "discipline": student.discipline or "", "recipient_count": len(parent_ids), "discount_ids": assigned, "discounts": [{"id": d.id, "name": d.name, "kind": d.kind, "value": d.value} for d in discount_rows], "active_subscription": is_subscription_active(student)})
    return {"clients": result, "disciplines": _broadcast_disciplines(club, students), "filters": {"discipline": discipline, "no_subscription": no_subscription, "no_visits_14": no_visits_14}}


@router.post("/webapp/admin-tariffs/change")
async def change_admin_tariff(payload: TariffChangePayload, request: Request, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, payload.club_id, with_for_update=True)
    if not club:
        raise HTTPException(404, "???? ?? ??????")
    tg_user = await verify_webapp_staff(club, payload.init_data, session, "tariffs_manage")
    settings = dict(club.club_settings or {})
    disciplines = dict(settings.get("disciplines", {}))

    if payload.action == "create_discipline":
        raw_name = str((payload.tariff or {}).get("name", "")).strip()
        if not raw_name:
            raise HTTPException(400, "Введите название дисциплины")
        code = "".join(ch.lower() if ch.isalnum() else "_" for ch in raw_name).strip("_")
        code = "_".join(part for part in code.split("_") if part)[:32] or "discipline"
        base_code = code
        suffix = 2
        while code in disciplines:
            code = f"{base_code[: max(1, 32 - len(str(suffix)) - 1)]}_{suffix}"
            suffix += 1
        disciplines[code] = {
            "name": raw_name,
            # Creation and publication are separate operations. The admin
            # must configure tariffs/schedule and explicitly enable it.
            "active": False,
            "type": "lessons",
            "schedule": {"mon": [], "tue": [], "wed": [], "thu": [], "fri": [], "sat": [], "sun": []},
            "tariffs": [],
        }
        settings["disciplines"] = disciplines
        await session.execute(update(Club).where(Club.id == club.id).values(club_settings=settings))
        await session.commit()
        redis = getattr(request.app.state, "redis_client", None)
        if redis:
            await redis.delete(f"club_config:{club.bot_token}")
        audit_event(
            "discipline_created",
            **await audit_actor_context(session, club, tg_user, "webapp/admin-tariffs/change"),
            club_id=club.id,
            action="create",
            object_type="discipline",
            object_id=code,
            discipline=code,
            tariff={"name": raw_name},
        )
        return {"success": True, "discipline_code": code, "active": False}

    block = dict(disciplines.get(payload.discipline, {}))
    if not block:
        raise HTTPException(404, "???????????? ?? ???????")
    tariffs = list(block.get("tariffs", []) or [])

    def _as_float(value, default=0.0):
        try:
            text = str(value).strip()
            return float(text) if text else float(default)
        except (TypeError, ValueError):
            return float(default)

    def _as_int(value, default=0):
        try:
            text = str(value).strip()
            return int(float(text)) if text else int(default)
        except (TypeError, ValueError):
            return int(default)

    if payload.action == "toggle_active":
        block["active"] = not bool(block.get("active", True))
    elif payload.action == "toggle_type":
        block["type"] = "lessons" if block.get("type", "lessons") == "unlimited" else "unlimited"
        if block["type"] == "unlimited":
            for tariff in tariffs:
                tariff["count"] = 999
    elif payload.action == "copy_from":
        source_disc = str((payload.tariff or {}).get("source_discipline", "")).strip()
        if not source_disc:
            raise HTTPException(400, "Источник копирования не указан")
        if source_disc == payload.discipline:
            raise HTTPException(400, "Нельзя копировать дисциплину в саму себя")
        source_block = dict(disciplines.get(source_disc, {}))
        if not source_block:
            raise HTTPException(404, "Источник копирования не найден")
        source_tariffs = copy.deepcopy(source_block.get("tariffs", []) or [])
        if not source_tariffs:
            raise HTTPException(400, "Р’ РёСЃС…РѕРґРЅРѕР№ РґРёСЃС†РёРїР»РёРЅРµ РЅРµС‚ С‚Р°СЂРёС„РѕРІ")
        block["type"] = source_block.get("type", block.get("type", "lessons"))
        block["tariffs"] = source_tariffs
        block["active"] = True
        disciplines[payload.discipline] = block
        settings["disciplines"] = disciplines
        await session.execute(update(Club).where(Club.id == club.id).values(club_settings=settings))
        await session.commit()
        redis = getattr(request.app.state, "redis_client", None)
        if redis:
            await redis.delete(f"club_config:{club.bot_token}")
        audit_event(
            "discipline_copied",
            **await audit_actor_context(session, club, tg_user, "webapp/admin-tariffs/change"),
            club_id=club.id,
            action="copy",
            object_type="discipline",
            object_id=payload.discipline,
            discipline=payload.discipline,
            source_discipline=source_disc,
        )
        return {"success": True, "copied_from": source_disc}
    elif payload.action == "add":
        tariff = payload.tariff or {}
        tariffs.append({"price": _as_float(tariff.get("price", 0)), "days": max(1, _as_int(tariff.get("days", 30), 30)), "count": 999 if block.get("type") == "unlimited" else max(0, _as_int(tariff.get("count", 0), 0)), "min_age": max(0, _as_int(tariff.get("min_age", 0), 0)), "active": True})
    elif payload.action in {"update", "delete"}:
        if payload.index is None or payload.index < 0 or payload.index >= len(tariffs):
            raise HTTPException(400, "????? ?? ??????")
        if payload.action == "delete":
            target = tariffs[payload.index]
            target_count = int(target.get("count", 0) or 0)
            target_days = int(target.get("days", 30) or 30)
            active_students = (await session.execute(select(Student).where(Student.club_id == club.id, Student.discipline == payload.discipline, Student.balance_lessons == target_count, Student.expire_date > datetime.utcnow()))).scalars().all()
            purchased_students = (await session.execute(select(Student).join(PaymentOrder, PaymentOrder.student_id == Student.id).where(Student.club_id == club.id, Student.discipline == payload.discipline, Student.expire_date > datetime.utcnow(), PaymentOrder.club_id == club.id, PaymentOrder.discipline == payload.discipline, PaymentOrder.lesson_count == target_count, PaymentOrder.days_to_add == target_days, PaymentOrder.status.in_(("CONFIRMED", "SUCCEEDED", "PAID"))).distinct())).scalars().all()
            if {student.id for student in active_students} | {student.id for student in purchased_students}:
                raise HTTPException(409, "Нельзя удалить тариф, который используется действующими атлетами")
            tariffs.pop(payload.index)
        else:
            tariff = payload.tariff or {}
            old_active = bool(tariffs[payload.index].get("active", True))
            tariffs[payload.index] = {"price": _as_float(tariff.get("price", 0)), "days": max(1, _as_int(tariff.get("days", 30), 30)), "count": 999 if block.get("type") == "unlimited" else max(0, _as_int(tariff.get("count", 0), 0)), "min_age": max(0, _as_int(tariff.get("min_age", 0), 0)), "active": old_active}
    else:
        raise HTTPException(400, "??????????? ????????")
    if any(_as_float(t.get("price", 0)) <= 0 or _as_int(t.get("days", 0)) <= 0 or _as_int(t.get("count", 0)) < 0 or _as_int(t.get("min_age", 0)) < 0 or _as_int(t.get("min_age", 0)) > 100 for t in tariffs):
        raise HTTPException(400, "????, ???? ? ?????????? ?????? ???? ??????????????")
    block["tariffs"] = tariffs
    disciplines[payload.discipline] = block
    settings["disciplines"] = disciplines
    await session.execute(update(Club).where(Club.id == club.id).values(club_settings=settings))
    await session.commit()
    redis = getattr(request.app.state, "redis_client", None)
    if redis:
        await redis.delete(f"club_config:{club.bot_token}")
    audit_event(
        "tariff_changed",
        **await audit_actor_context(session, club, tg_user, "webapp/admin-tariffs/change"),
        club_id=club.id,
        action=payload.action,
        object_type="tariff",
        object_id=payload.discipline,
        discipline=payload.discipline,
        tariff_index=payload.index,
        tariff=payload.tariff,
    )
    return {"success": True}

@router.get("/webapp/admin-motivation", response_class=HTMLResponse)
async def admin_motivation_page(request: Request, club_id: int = Query(...), date_from: str | None = Query(None), date_to: str | None = Query(None), init_data: str | None = Query(None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data:
        return telegram_init_gate("/webapp/admin-motivation", club_id, "Откройте мотивацию тренеров из Telegram")
    tg_user = await verify_webapp_staff(club, init_data, session, "schedule_view")
    # Legacy fallback template name is retained for compatibility with feature-gating checks.
    motivation_fallback_template = "motivation_unavailable.html"
    if int(tg_user.get("id", 0)) != int(getattr(club, "owner_id", 0) or 0) and int(tg_user.get("id", 0)) not in SUPER_ADMIN_IDS:
        raise HTTPException(403, "Раздел мотивации доступен только администратору")
    # Единый рабочий экран мотивации: правила занятия и начисления.
    staff = (await session.execute(select(ClubStaff).where(ClubStaff.club_id == club_id, ClubStaff.is_active.is_(True)))).scalars().all()
    day_names = {"mon": "Пн", "tue": "Вт", "wed": "Ср", "thu": "Чт", "fri": "Пт", "sat": "Сб", "sun": "Вс"}
    motivation_lessons = []
    for discipline, block in (club.club_settings or {}).get("disciplines", {}).items():
        for day, lessons in (block or {}).get("schedule", {}).items():
            for index, lesson in enumerate(lessons or []):
                motivation_lessons.append({
                    "discipline": discipline,
                    "discipline_name": (block or {}).get("name") or discipline,
                    "day": day,
                    "day_name": day_names.get(day, day),
                    "index": index,
                    "time": lesson.get("time", ""),
                    "duration_minutes": lesson.get("duration_minutes", lesson.get("duration", 60)),
                    "coach_staff_ids": lesson.get("coach_staff_ids") or ([lesson.get("coach_staff_id")] if lesson.get("coach_staff_id") else []),
                    "coaches": [next((x.full_name for x in staff if x.id == int(staff_id)), f"Тренер #{staff_id}") for staff_id in (lesson.get("coach_staff_ids") or ([lesson.get("coach_staff_id")] if lesson.get("coach_staff_id") else []))],
                    "rules": lesson.get("motivation_rules") or [],
                })
    accruals = list((await session.execute(select(MotivationAccrual).where(MotivationAccrual.club_id == club_id).order_by(MotivationAccrual.occurrence_date.desc(), MotivationAccrual.start_time.desc()).limit(100))).scalars().all())
    return templates.TemplateResponse("admin_motivation.html", {"request": request, "club_id": club_id, "init_data": init_data or "", "lessons": motivation_lessons, "accruals": accruals})

@router.post("/webapp/admin-motivation/rules")
async def save_admin_motivation_rules(payload: dict, session: AsyncSession = Depends(get_session)):
    club = (await session.execute(select(Club).where(Club.id == int(payload.get("club_id", 0))).with_for_update())).scalar_one_or_none()
    tg_user = await verify_webapp_staff(club, payload.get("init_data"), session, "schedule_edit")
    if int(tg_user.get("id", 0)) != int(getattr(club, "owner_id", 0) or 0) and int(tg_user.get("id", 0)) not in SUPER_ADMIN_IDS:
        raise HTTPException(403, "Только администратор может менять мотивацию")
    discipline = str(payload.get("discipline", "")).strip(); day = str(payload.get("day", "")).strip(); index = int(payload.get("index", -1))
    settings = dict(club.club_settings or {}); block = dict(settings.get("disciplines", {}).get(discipline, {})); lessons = list((block.get("schedule", {}) or {}).get(day, []))
    if index < 0 or index >= len(lessons): raise HTTPException(404, "Занятие не найдено")
    rules = []
    for raw in payload.get("rules", []) or []:
        minimum = int(raw.get("min_students", 0)); maximum = int(raw.get("max_students", 0)); rate = int(raw.get("rate_kopecks", 0))
        if minimum < 1 or maximum < minimum or rate < 0: raise HTTPException(400, "Некорректный диапазон или ставка")
        rules.append({"min_students": minimum, "max_students": maximum, "rate_kopecks": rate})
    rules.sort(key=lambda item: item["min_students"])
    lessons[index] = dict(lessons[index]); lessons[index]["motivation_rules"] = rules; block["schedule"] = dict(block.get("schedule", {})); block["schedule"][day] = lessons
    settings["disciplines"] = dict(settings.get("disciplines", {})); settings["disciplines"][discipline] = block; club.club_settings = settings
    await session.commit(); return {"success": True, "rules": rules}

@router.get("/webapp/staff-motivation", response_class=HTMLResponse)
async def staff_motivation_page(request: Request, club_id: int = Query(...), date_from: str | None = Query(None), date_to: str | None = Query(None), init_data: str | None = Query(None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data:
        return telegram_init_gate("/webapp/staff-motivation", club_id, "Откройте мотивацию из Telegram")
    tg_user = verify_telegram_data(init_data, club.bot_token if club else "")
    if not tg_user:
        raise HTTPException(403, "Недействительные данные Telegram")
    user_id = int(tg_user.get("id", 0))
    staff = await session.scalar(select(ClubStaff).where(ClubStaff.club_id == club_id, ClubStaff.telegram_id == user_id, ClubStaff.is_active.is_(True)))
    is_admin = user_id == int(getattr(club, "owner_id", 0) or 0) or user_id in SUPER_ADMIN_IDS
    if not staff and not is_admin:
        raise HTTPException(403, "Раздел доступен только сотрудникам")
    today = datetime.now(_MOTIVATION_TZ).date()
    try:
        start = date.fromisoformat(date_from) if date_from else today.replace(day=1)
        end = date.fromisoformat(date_to) if date_to else today
    except ValueError:
        raise HTTPException(400, "Диапазон дат должен быть в формате ГГГГ-ММ-ДД")
    if end < start or (end - start).days > 366:
        raise HTTPException(400, "Недопустимый диапазон дат")
    rows_query = select(MotivationAccrual).where(MotivationAccrual.club_id == club_id, MotivationAccrual.occurrence_date >= start, MotivationAccrual.occurrence_date <= end).order_by(MotivationAccrual.occurrence_date.desc(), MotivationAccrual.start_time.desc())
    rows = list((await session.execute(rows_query)).scalars().all())
    if not is_admin:
        rows = [row for row in rows if staff.id in (row.staff_ids or [])]
    total = sum(int(row.rate_kopecks or 0) for row in rows)
    return templates.TemplateResponse("staff_motivation.html", {"request": request, "club_id": club_id, "rows": rows, "total": total, "is_admin": is_admin, "date_from": start.isoformat(), "date_to": end.isoformat()})
    staff = (await session.execute(select(ClubStaff).where(ClubStaff.club_id == club_id).order_by(ClubStaff.full_name))).scalars().all()
    now_local = datetime.now(_MOTIVATION_TZ)
    today = now_local.date()
    try:
        start = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else today.replace(day=1)
        end = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else today.replace(day=calendar.monthrange(today.year, today.month)[1])
    except ValueError:
        raise HTTPException(400, "Диапазон дат должен быть в формате ГГГГ-ММ-ДД")
    if end < start or (end - start).days > 366:
        raise HTTPException(400, "Допустимый диапазон — от 1 до 366 дней")
    occurrences = _motivation_occurrences(club.club_settings or {}, start, end)
    adjustments = (await session.execute(select(MotivationAdjustment).where(MotivationAdjustment.club_id == club_id))).scalars().all()
    rates = (await session.execute(select(MotivationRate).where(MotivationRate.club_id == club_id))).scalars().all()
    rate_by_key = {(x.staff_id, x.discipline): x for x in rates}
    individuals = (await session.execute(select(MotivationIndividual).where(MotivationIndividual.club_id == club_id, MotivationIndividual.training_date >= start, MotivationIndividual.training_date <= end))).scalars().all()
    adjustments = [x for x in adjustments if start <= (_motivation_local_date(x.created_at) or start) <= end]
    existing = (await session.execute(select(MotivationAccrual).where(MotivationAccrual.club_id == club_id, MotivationAccrual.occurrence_date >= start, MotivationAccrual.occurrence_date <= min(end, today)))).scalars().all()
    rows = []
    for coach in staff:
        own = [x for x in occurrences if x["staff_id"] == coach.id]
        completed = [x for x in own if _motivation_occurrence_ended(x, now_local)]
        future = [x for x in own if not _motivation_occurrence_ended(x, now_local)]
        correction = sum(x.amount_kopecks for x in adjustments if x.staff_id == coach.id)
        earned = sum(x.rate_kopecks + x.bonus_kopecks for x in existing if x.staff_id == coach.id)
        own_individuals = [x for x in individuals if x.staff_id == coach.id]
        individual_earned = sum(x.rate_kopecks for x in own_individuals if _motivation_occurrence_ended(_individual_motivation_occurrence(x), now_local))
        individual_future = sum(x.rate_kopecks for x in own_individuals if not _motivation_occurrence_ended(_individual_motivation_occurrence(x), now_local))
        future_pay = sum(_scheduled_rate(rate_by_key.get((coach.id, x["discipline"])), x["date"], coach.rate_per_training_kopecks) for x in future)
        rows.append({"id": coach.id, "name": coach.full_name or f"Тренер #{coach.id}", "rate": coach.rate_per_training_kopecks or 0, "completed": len(completed) + sum(_motivation_occurrence_ended(_individual_motivation_occurrence(x), now_local) for x in own_individuals), "forecast_count": len(future) + sum(not _motivation_occurrence_ended(_individual_motivation_occurrence(x), now_local) for x in own_individuals), "earned": earned + individual_earned + correction, "forecast": future_pay + individual_future + earned + individual_earned + correction, "correction": correction})
    return templates.TemplateResponse("admin_motivation.html", {"request": request, "club_id": club_id, "date_from": start.isoformat(), "date_to": end.isoformat(), "today": today.isoformat(), "month": f"{start:%d.%m.%Y} — {end:%d.%m.%Y}", "staff": rows})

@router.post("/webapp/admin-motivation/adjust")
async def adjust_admin_motivation(payload: dict, session: AsyncSession = Depends(get_session)):
    raise HTTPException(410, "Мотивация удалена и будет заменена новой логикой")
    club = await session.get(Club, int(payload.get("club_id", 0)))
    tg_user = await verify_webapp_staff(club, payload.get("init_data"), session, "schedule_view")
    if int(tg_user.get("id", 0)) != int(getattr(club, "owner_id", 0) or 0) and int(tg_user.get("id", 0)) not in SUPER_ADMIN_IDS:
        raise HTTPException(403, "Только администратор может корректировать мотивацию")
    staff = await session.get(ClubStaff, int(payload.get("staff_id", 0)))
    if not staff or staff.club_id != club.id:
        raise HTTPException(404, "Тренер не найден")
    try:
        amount = int(payload.get("amount_kopecks", 0)); rate = int(payload.get("rate_per_training_kopecks", staff.rate_per_training_kopecks or 0)); bonus = max(0, int(payload.get("bonus_per_student_kopecks", 0))); threshold = max(1, min(100, int(payload.get("bonus_threshold", 5))))
        weekday_rate = max(0, int(payload.get("weekday_rate_kopecks", rate)))
        weekend_rate = max(0, int(payload.get("weekend_rate_kopecks", rate)))
    except (TypeError, ValueError):
        raise HTTPException(400, "Некорректная сумма")
    reason = str(payload.get("reason", "")).strip()
    if not reason:
        raise HTTPException(400, "Укажите причину корректировки")
    idempotency_key = str(payload.get("idempotency_key", "")).strip()[:80]
    if amount and not idempotency_key:
        raise HTTPException(400, "Для корректировки нужен idempotency_key")
    if idempotency_key:
        previous = (await session.execute(select(MotivationAdjustment).where(MotivationAdjustment.idempotency_key == idempotency_key))).scalar_one_or_none()
        if previous:
            return {"success": True, "duplicate": True}
    discipline = str(payload.get("discipline", "")).strip()[:50]
    if discipline:
        rate_row = (await session.execute(select(MotivationRate).where(MotivationRate.club_id == club.id, MotivationRate.staff_id == staff.id, MotivationRate.discipline == discipline))).scalar_one_or_none()
        if rate_row:
            rate_row.rate_kopecks = max(0, rate)
            rate_row.bonus_threshold = threshold
            rate_row.bonus_per_student_kopecks = bonus
            rate_row.weekday_rate_kopecks = weekday_rate
            rate_row.weekend_rate_kopecks = weekend_rate
        else:
            session.add(MotivationRate(club_id=club.id, staff_id=staff.id, discipline=discipline, rate_kopecks=max(0, rate), bonus_threshold=threshold, bonus_per_student_kopecks=bonus, weekday_rate_kopecks=weekday_rate, weekend_rate_kopecks=weekend_rate))
    else:
        staff.rate_per_training_kopecks = max(0, rate)
    if amount:
        session.add(MotivationAdjustment(club_id=club.id, staff_id=staff.id, amount_kopecks=amount, reason=reason[:500], idempotency_key=idempotency_key))
    await session.commit()
    return {"success": True}

@router.post("/webapp/admin-motivation/individual")
async def add_individual_motivation(payload: dict, session: AsyncSession = Depends(get_session)):
    raise HTTPException(410, "Мотивация удалена и будет заменена новой логикой")
    club = await session.get(Club, int(payload.get("club_id", 0)))
    tg_user = await verify_webapp_staff(club, payload.get("init_data"), session, "schedule_view")
    if int(tg_user.get("id", 0)) != int(getattr(club, "owner_id", 0) or 0) and int(tg_user.get("id", 0)) not in SUPER_ADMIN_IDS:
        raise HTTPException(403, "Только администратор")
    key = str(payload.get("idempotency_key", "")).strip()[:100]
    if not key:
        raise HTTPException(400, "Нужен idempotency_key")
    if await session.scalar(select(MotivationIndividual).where(MotivationIndividual.idempotency_key == key)):
        return {"success": True, "duplicate": True}
    try:
        training_date = datetime.strptime(str(payload.get("training_date")), "%Y-%m-%d").date()
        rate = int(payload.get("rate_kopecks", 0)); duration = int(payload.get("duration_minutes", 60))
    except (TypeError, ValueError):
        raise HTTPException(400, "Некорректные дата, ставка или длительность")
    if rate < 0 or duration < 15 or duration > 240:
        raise HTTPException(400, "Длительность должна быть от 15 до 240 минут")
    start_time = _parse_motivation_time(payload.get("start_time", "00:00"))
    staff = await session.get(ClubStaff, int(payload.get("staff_id", 0)))
    if not staff or staff.club_id != club.id or not staff.is_active:
        raise HTTPException(404, "Тренер не найден")
    session.add(MotivationIndividual(club_id=club.id, staff_id=staff.id, training_date=training_date, start_time=start_time, duration_minutes=duration, rate_kopecks=rate, idempotency_key=key, note=str(payload.get("note", ""))[:500]))
    await session.commit()
    return {"success": True}

@router.get("/webapp/admin-motivation/export")
async def export_admin_motivation(club_id: int = Query(...), date_from: str | None = Query(None), date_to: str | None = Query(None), init_data: str | None = Query(None), session: AsyncSession = Depends(get_session)):
    raise HTTPException(410, "Экспорт мотивации удалён и будет заменён новой логикой")
    club = await session.get(Club, club_id); tg_user = await verify_webapp_staff(club, init_data, session, "schedule_view")
    if int(tg_user.get("id", 0)) != int(getattr(club, "owner_id", 0) or 0) and int(tg_user.get("id", 0)) not in SUPER_ADMIN_IDS: raise HTTPException(403, "Только администратор")
    today = datetime.now(_MOTIVATION_TZ).date(); start = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else today.replace(day=1); end = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else today
    if end < start or (end - start).days > 366: raise HTTPException(400, "Недопустимый диапазон")
    rows = (await session.execute(select(MotivationAccrual, ClubStaff.full_name).join(ClubStaff, ClubStaff.id == MotivationAccrual.staff_id).where(MotivationAccrual.club_id == club_id, MotivationAccrual.occurrence_date.between(start, end)).order_by(MotivationAccrual.occurrence_date))).all()
    individuals = (await session.execute(select(MotivationIndividual, ClubStaff.full_name).join(ClubStaff, ClubStaff.id == MotivationIndividual.staff_id).where(MotivationIndividual.club_id == club_id, MotivationIndividual.training_date.between(start, end)))).all()
    adjustments = (await session.execute(select(MotivationAdjustment, ClubStaff.full_name).join(ClubStaff, ClubStaff.id == MotivationAdjustment.staff_id).where(MotivationAdjustment.club_id == club_id))).all()
    from openpyxl import Workbook
    output = BytesIO(); book = Workbook(); sheet = book.active; sheet.title = "Мотивация"; sheet.append(["Дата", "Тренер", "Дисциплина", "Ученики", "База", "Бонус", "Итого"])
    for item, name in rows: sheet.append([item.occurrence_date, name, item.discipline, item.student_count, item.rate_kopecks / 100, item.bonus_kopecks / 100, (item.rate_kopecks + item.bonus_kopecks) / 100])
    for item, name in individuals: sheet.append([item.training_date, name, "Индивидуальная", "", "", "", item.rate_kopecks / 100])
    for item, name in adjustments:
        adjustment_date = _motivation_local_date(item.created_at)
        if adjustment_date and start <= adjustment_date <= end:
            sheet.append([adjustment_date, name, "Корректировка", "", "", "", item.amount_kopecks / 100])
    book.save(output); output.seek(0)
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="motivation_{club_id}_{start}_{end}.xlsx"'})

@router.get("/webapp/admin-schedule", response_class=HTMLResponse)
async def webapp_admin_schedule_page(
        request: Request,
        club_id: int = Query(...),
        session: AsyncSession = Depends(get_session),
        init_data: str | None = Query(default=None),
):
    club = (await session.execute(select(Club).where(Club.id == club_id))).scalar_one_or_none()
    if not init_data:
        return telegram_init_gate('/webapp/admin-schedule', club_id, 'РћС‚РєСЂРѕР№С‚Рµ Р°РґРјРёРЅСЃРєРѕРµ СЂР°СЃРїРёСЃР°РЅРёРµ РёР· Telegram')
    tg_user = await verify_webapp_staff(club, init_data, session, "schedule_view")
    is_admin = int(tg_user.get("id", 0)) == int(getattr(club, "owner_id", 0) or 0) or int(tg_user.get("id", 0)) in SUPER_ADMIN_IDS
    staff = (await session.execute(select(ClubStaff).where(ClubStaff.club_id == club_id, ClubStaff.is_active.is_(True)).order_by(ClubStaff.full_name))).scalars().all()
    staff_data = [{"id": x.id, "name": x.full_name or f"Тренер #{x.id}"} for x in staff]
    return templates.TemplateResponse("admin_schedule.html", {"request": request, "club": club, "club_id": club_id, "disciplines": (club.club_settings or {}).get("disciplines", {}), "staff": staff_data, "can_assign_coach": is_admin, "schedule_effective_date": datetime.now(_MOTIVATION_TZ).date().isoformat()})


@router.get("/webapp/admin-coach-motivation", response_class=HTMLResponse)
async def webapp_admin_coach_motivation_page(
        request: Request,
        club_id: int = Query(...),
        session: AsyncSession = Depends(get_session),
        init_data: str | None = Query(default=None),
):
    target = f"/webapp/admin-motivation?club_id={club_id}&init_data={quote(init_data or '')}"
    return RedirectResponse(target, status_code=307)


@router.post("/webapp/admin-coach-motivation/save")
async def save_admin_coach_motivation(request: Request, session: AsyncSession = Depends(get_session)):
    payload = await request.json()
    club_id = int(payload.get("club_id") or 0)
    init_data = payload.get("init_data")
    club = (await session.execute(select(Club).where(Club.id == club_id).with_for_update())).scalar_one_or_none()
    tg_user = await verify_webapp_admin(club, init_data)
    settings = dict(club.club_settings or {})
    disciplines = settings.get("disciplines", {}) if isinstance(settings.get("disciplines", {}), dict) else {}

    raw_rules = payload.get("disciplines", {})
    if not isinstance(raw_rules, dict):
        raise HTTPException(400, "Некорректные настройки дисциплин")

    def money(value, default=0):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return default
        if value < 0 or value > 10_000_000:
            raise HTTPException(400, "Сумма должна быть от 0 до 10 000 000 ₽")
        return round(value, 2)

    clean_rules = {}
    for code, rule in raw_rules.items():
        if code not in disciplines or not isinstance(rule, dict):
            raise HTTPException(400, "Можно настраивать только существующие дисциплины")
        threshold = int(rule.get("low_attendance_threshold", 10) or 10)
        if threshold < 0 or threshold > 1000:
            raise HTTPException(400, "Порог учеников должен быть от 0 до 1000")
        multiplier = float(rule.get("low_attendance_multiplier", 0.5) or 0.5)
        if multiplier < 0 or multiplier > 1:
            raise HTTPException(400, "Коэффициент должен быть от 0 до 1")
        clean_rules[str(code)] = {
            "base_rate": money(rule.get("base_rate")),
            "bonus_per_student": money(rule.get("bonus_per_student")),
            "bonus_per_session": money(rule.get("bonus_per_session")),
            "low_attendance_threshold": threshold,
            "low_attendance_multiplier": round(multiplier, 4),
            "enabled": bool(rule.get("enabled", True)),
        }

    personal_training = money(payload.get("personal_training_rate"))
    period_from = str(payload.get("period_from") or "")[:10]
    period_to = str(payload.get("period_to") or "")[:10]
    if period_from and period_to and period_from > period_to:
        raise HTTPException(400, "Дата начала периода не может быть позже даты окончания")

    staff_rows = payload.get("staff", {})
    if not isinstance(staff_rows, dict):
        raise HTTPException(400, "Некорректные настройки персонала")
    staff_ids = {str(x) for x in (await session.scalars(select(ClubStaff.id).where(ClubStaff.club_id == club.id, ClubStaff.is_active.is_(True)))).all()}
    clean_staff = {}
    for staff_id, row in staff_rows.items():
        if str(staff_id) not in staff_ids or not isinstance(row, dict):
            raise HTTPException(400, "Можно назначать только активный персонал этого клуба")
        mode = str(row.get("mode") or "coach").lower()
        if mode not in {"coach", "fixed_day", "fixed_month"}:
            raise HTTPException(400, "Некорректный режим оплаты сотрудника")
        assigned = row.get("disciplines", [])
        if not isinstance(assigned, list):
            raise HTTPException(400, "Некорректный список дисциплин сотрудника")
        clean_staff[str(staff_id)] = {
            "mode": mode,
            "rate": money(row.get("rate")),
            "disciplines": [str(x) for x in assigned if str(x) in disciplines],
        }
    settings["coach_motivation"] = {
        "enabled": bool(payload.get("enabled", True)),
        "personal_training_rate": personal_training,
        "period_from": period_from,
        "period_to": period_to,
        "disciplines": clean_rules,
        "staff": clean_staff,
        "updated_at": datetime.utcnow().isoformat(),
    }
    club.club_settings = settings
    await session.commit()
    audit_event(
        "coach_motivation_settings_updated",
        **await audit_actor_context(session, club, tg_user, "webapp/admin-coach-motivation"),
        club_id=club.id,
        action="update",
        object_type="coach_motivation",
        disciplines=list(clean_rules),
    )
    return {"ok": True, "disciplines": clean_rules, "personal_training_rate": personal_training}

@router.post("/webapp/admin-schedule/change")
async def change_admin_schedule(payload: ScheduleChangePayload, session: AsyncSession = Depends(get_session)):
    club = (await session.execute(select(Club).where(Club.id == payload.club_id).with_for_update())).scalar_one_or_none()
    tg_user = await verify_webapp_staff(club, payload.init_data, session, "schedule_edit")
    is_admin = int(tg_user.get("id", 0)) == int(getattr(club, "owner_id", 0) or 0) or int(tg_user.get("id", 0)) in SUPER_ADMIN_IDS
    settings = dict(club.club_settings or {})
    disciplines = dict(settings.get("disciplines", {}))
    previous_disciplines = copy.deepcopy(disciplines)
    try:
        effective_date = date.fromisoformat(str(payload.effective_date or datetime.now(_MOTIVATION_TZ).date().isoformat()))
    except (TypeError, ValueError):
        raise HTTPException(400, "Дата начала действия должна быть в формате ГГГГ-ММ-ДД")
    if effective_date < date(1970, 1, 1) or effective_date > date(2100, 12, 31):
        raise HTTPException(400, "Недопустимая дата начала действия")
    block = dict(disciplines.get(payload.discipline, {}))
    if not block:
        raise HTTPException(404, "Дисциплина не найдена")
    if payload.day not in {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}:
        raise HTTPException(400, "Некорректный день недели")
    schedule = normalize_schedule_block(block.get("schedule", {}))
    lessons = list(schedule.get(payload.day, []))
    if payload.action == "delete":
        if payload.index is None or payload.index < 0 or payload.index >= len(lessons):
            raise HTTPException(400, "Занятие не найдено")
        lessons.pop(payload.index)
    elif payload.action == "copy_day":
        source_day = (payload.source_day or "").strip()
        source_lessons = list(schedule.get(source_day, []))
        if not source_day:
            raise HTTPException(400, "Источник копирования не указан")
        if source_day == payload.day:
            raise HTTPException(400, "Нельзя копировать день в сам себя")
        if not source_lessons:
            raise HTTPException(400, "В исходном дне нет занятий")
        lessons = [copy.deepcopy(lesson) for lesson in source_lessons]
    elif payload.action == "copy_from":
        source_disc = (payload.source_discipline or "").strip()
        if not source_disc:
            raise HTTPException(400, "Источник копирования не указан")
        if source_disc == payload.discipline:
            raise HTTPException(400, "Нельзя копировать дисциплину в саму себя")
        source_block = dict(disciplines.get(source_disc, {}))
        if not source_block:
            raise HTTPException(404, "Источник копирования не найден")
        source_schedule = normalize_schedule_block(source_block.get("schedule", {}))
        if not any(source_schedule.values()):
            raise HTTPException(400, "В исходной дисциплине нет занятий")
        schedule = {day_key: [copy.deepcopy(lesson) for lesson in source_schedule.get(day_key, [])] for day_key in schedule.keys()}
        block["schedule"] = schedule
        disciplines[payload.discipline] = block
        settings["disciplines"] = disciplines
        _remember_motivation_schedule_change(settings, previous_disciplines, disciplines, effective_date)
        club.club_settings = settings
        session.add(club)
        await session.commit()
        audit_event(
            "schedule_copied",
            **await audit_actor_context(session, club, tg_user, "webapp/admin-schedule/change"),
            action="copy",
            object_type="schedule",
            object_id=payload.discipline,
            discipline=payload.discipline,
            source_discipline=source_disc,
            effective_date=effective_date.isoformat(),
        )
        return {"success": True}
    elif payload.action in {"add", "update"}:
        lesson = payload.lesson or {}
        raw_duration = lesson.get("duration_minutes", lesson.get("duration", 60))
        try:
            parsed_duration = int(raw_duration if raw_duration is not None else 60)
        except (ValueError, TypeError):
            parsed_duration = 60
        if parsed_duration < 15 or parsed_duration > 240:
            raise HTTPException(400, "Длительность должна быть от 15 до 240 минут")
        item = {
            "time": str(lesson.get("time", "00:00"))[:5],
            "coach": str(lesson.get("info", lesson.get("coach", "")))[:100],
            "duration_minutes": parsed_duration,
        }
        selected_staff_ids = payload.coach_staff_ids if payload.coach_staff_ids is not None else ([payload.coach_staff_id] if payload.coach_staff_id is not None else None)
        if selected_staff_ids is not None:
            if not is_admin:
                raise HTTPException(403, "Назначать тренера может только администратор")
            selected_staff_ids = list(dict.fromkeys(int(x) for x in selected_staff_ids))
            if len(selected_staff_ids) > 5:
                raise HTTPException(400, "На одно занятие можно назначить не более 5 тренеров")
            assigned_staff = (await session.execute(select(ClubStaff).where(ClubStaff.club_id == club.id, ClubStaff.id.in_(selected_staff_ids), ClubStaff.is_active.is_(True)))).scalars().all()
            if len(assigned_staff) != len(selected_staff_ids):
                raise HTTPException(400, "Выберите действующих тренеров клуба")
            names = {x.id: x.full_name or f"Тренер #{x.id}" for x in assigned_staff}
            item["coach_staff_ids"] = selected_staff_ids
            item["coach_staff_id"] = selected_staff_ids[0] if selected_staff_ids else None
            # `coach` is the free-form lesson information/comment. The
            # assigned trainers live only in coach_staff_ids and must not
            # overwrite that comment.
            previous_lesson = lessons[payload.index] if payload.action == "update" and payload.index is not None and 0 <= payload.index < len(lessons) else {}
            previous_comment = str(previous_lesson.get("coach", "")).strip()
            assigned_names = {str(names[x]).strip().casefold() for x in selected_staff_ids}
            if previous_comment.casefold() in assigned_names or previous_comment.casefold() == ", ".join(names[x] for x in selected_staff_ids).casefold():
                item["coach"] = ""
        elif payload.action == "update" and payload.index is not None and payload.index < len(lessons):
            previous_lesson = lessons[payload.index]
            if previous_lesson.get("coach_staff_ids"):
                item["coach_staff_ids"] = list(previous_lesson["coach_staff_ids"])
            item["coach_staff_id"] = lessons[payload.index].get("coach_staff_id")
        hour_minute = str(item["time"]).split(":")
        if len(hour_minute) != 2 or not all(part.isdigit() for part in hour_minute) or not (0 <= int(hour_minute[0]) <= 23 and 0 <= int(hour_minute[1]) <= 59):
            raise HTTPException(400, "Время должно быть в формате ЧЧ:ММ")
        if payload.action == "add": lessons.append(item)
        elif payload.index is not None and 0 <= payload.index < len(lessons): lessons[payload.index] = item
        else: raise HTTPException(400, "Р—Р°РЅСЏС‚РёРµ РЅРµ РЅР°Р№РґРµРЅРѕ")
    else: raise HTTPException(400, "РќРµРёР·РІРµСЃС‚РЅРѕРµ РґРµР№СЃС‚РІРёРµ")
    lessons.sort(key=lambda x: str(x.get("time", "99:99")))
    schedule[payload.day] = lessons; block["schedule"] = schedule; disciplines[payload.discipline] = block; settings["disciplines"] = disciplines
    _remember_motivation_schedule_change(settings, previous_disciplines, disciplines, effective_date)
    club.club_settings = settings
    await session.commit()
    audit_event(
        "schedule_changed",
        **await audit_actor_context(session, club, tg_user, "webapp/admin-schedule/change"),
        club_id=club.id,
        action=payload.action,
        object_type="schedule",
        object_id=f"{payload.discipline}:{payload.day}",
        discipline=payload.discipline,
        day=payload.day,
        lesson=payload.lesson,
        index=payload.index,
        source_day=payload.source_day,
        effective_date=effective_date.isoformat(),
    )
    return {"success": True}


@router.get("/webapp/admin-work-schedule", response_class=HTMLResponse)
async def webapp_admin_work_schedule_page(
        request: Request,
        club_id: int = Query(...),
        session: AsyncSession = Depends(get_session),
        init_data: str | None = Query(default=None),
):
    club = await session.get(Club, club_id)
    if not init_data:
        return telegram_init_gate('/webapp/admin-work-schedule', club_id, 'РћС‚РєСЂРѕР№С‚Рµ РіСЂР°С„РёРє СЂР°Р±РѕС‚С‹ РёР· Telegram')
    await verify_webapp_staff(club, init_data, session, "settings_manage")
    settings = club.club_settings if isinstance(club.club_settings, dict) else {}
    work_schedule = settings.get("work_schedule", {})
    return templates.TemplateResponse("admin_work_schedule.html", {"request": request, "club": club, "club_id": club_id, "work_schedule": work_schedule})


@router.post("/webapp/admin-work-schedule/change")
async def change_admin_work_schedule(payload: dict, session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, int(payload.get("club_id", 0)))
    if not club:
        raise HTTPException(404, "РљР»СѓР± РЅРµ РЅР°Р№РґРµРЅ")
    tg_user = await verify_webapp_staff(club, payload.get("init_data"), session, "settings_manage")
    settings = dict(club.club_settings or {})
    work_schedule = dict(settings.get("work_schedule", {}))
    day = str(payload.get("day", "")).strip()
    if day not in {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}:
        raise HTTPException(400, "РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ РґРµРЅСЊ")
    action = str(payload.get("action", "")).strip()
    item = payload.get("item") or {}
    if action in {"add", "update"}:
        open_at = str(item.get("open", "09:00"))[:5]
        close_at = str(item.get("close", "18:00"))[:5]
        note = str(item.get("note", ""))[:180]
        def _minutes(value: str) -> int | None:
            parts = value.split(":")
            if len(parts) != 2 or not all(part.isdigit() for part in parts):
                return None
            hour, minute = (int(part) for part in parts)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                return None
            return hour * 60 + minute
        open_minutes = _minutes(open_at)
        close_minutes = _minutes(close_at)
        if open_minutes is None or close_minutes is None or open_minutes >= close_minutes:
            raise HTTPException(400, "Время открытия и закрытия должно быть в формате ЧЧ:ММ, закрытие позже открытия")
        work_schedule[day] = {"open": open_at, "close": close_at, "note": note}
    elif action == "delete":
        work_schedule.pop(day, None)
    else:
        raise HTTPException(400, "РќРµРёР·РІРµСЃС‚РЅРѕРµ РґРµР№СЃС‚РІРёРµ")
    settings["work_schedule"] = work_schedule
    club.club_settings = settings
    await session.commit()
    audit_event(
        "work_schedule_changed",
        **await audit_actor_context(session, club, tg_user, "webapp/admin-work-schedule/change"),
        club_id=club.id,
        action=action,
        object_type="work_schedule",
        object_id=day,
        day=day,
        work_schedule=work_schedule.get(day),
    )
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
        return HTMLResponse(content="<h1>вќЊ РћС€РёР±РєР°: РќРµ СѓРґР°Р»РѕСЃСЊ РѕРїСЂРµРґРµР»РёС‚СЊ ID РєР»СѓР±Р°</h1>", status_code=400)

    stmt = select(Club).where(Club.id == club_id)
    result = await session.execute(stmt)
    club = result.scalar_one_or_none()

    if not club:
        return HTMLResponse(content="<h1>рџЏ° РљР»СѓР± РЅРµ РЅР°Р№РґРµРЅ РІ СЃРёСЃС‚РµРјРµ SpeedyCRM</h1>", status_code=404)
    # РџСѓР±Р»РёС‡РЅР°СЏ РєР»РёРµРЅС‚СЃРєР°СЏ СЃС‚СЂР°РЅРёС†Р°: С‚РѕР»СЊРєРѕ С‡С‚РµРЅРёРµ, Р±РµР· Telegram-Р°СѓС‚РµРЅС‚РёС„РёРєР°С†РёРё.

    settings = club.club_settings if isinstance(club.club_settings, dict) else {}
    disciplines_data = settings.get("disciplines", {})
    staff_rows = (await session.execute(select(ClubStaff).where(ClubStaff.club_id == club_id))).scalars().all()
    staff_names = {item.id: item.full_name or f"Тренер #{item.id}" for item in staff_rows}

    day_names = {
        "mon": "РџРѕРЅРµРґРµР»СЊРЅРёРє", "tue": "Р’С‚РѕСЂРЅРёРє", "wed": "РЎСЂРµРґР°",
        "thu": "Р§РµС‚РІРµСЂРі", "fri": "РџСЏС‚РЅРёС†Р°", "sat": "РЎСѓР±Р±РѕС‚Р°", "sun": "Р’РѕСЃРєСЂРµСЃРµРЅСЊРµ"
    }

    # РџР°СЂСЃРёРј JSON-РЅР°СЃС‚СЂРѕР№РєРё РєР»СѓР±Р° РІ СЃС‚СЂСѓРєС‚СѓСЂРёСЂРѕРІР°РЅРЅС‹Р№ СЃРїРёСЃРѕРє РґР»СЏ Jinja2 С€Р°Р±Р»РѕРЅР°
    parsed_disciplines = []

    if isinstance(disciplines_data, dict):
        for disc_key, disc_content in disciplines_data.items():
            if not isinstance(disc_content, dict):
                continue
            if not disc_content.get("active", False):
                continue

            # Название дисциплины берём только из блока дисциплины. Данные
            # занятия (включая назначенного тренера) не должны влиять на него.
            disc_name = str(disc_content.get("name") or disc_key)
            schedule_data = normalize_schedule_block(disc_content.get("schedule", {}))

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

                    assigned_ids = lesson.get("coach_staff_ids") or ([lesson["coach_staff_id"]] if lesson.get("coach_staff_id") else [])
                    trainer_labels = [staff_names.get(int(staff_id), f"Тренер #{staff_id}") for staff_id in assigned_ids]
                    lesson_comment = str(lesson.get("coach", "РРЅСЃС‚СЂСѓРєС‚РѕСЂ"))
                    legacy_comments = {name.strip().casefold() for name in trainer_labels}
                    if lesson_comment.strip().casefold() in legacy_comments or lesson_comment.strip().casefold() == ", ".join(trainer_labels).casefold():
                        lesson_comment = ""
                    parsed_lessons.append({
                        "time": str(lesson.get("time", "00:00")),
                        "coach": lesson_comment,
                        "trainers": ", ".join(trainer_labels),
                        "duration_minutes": int(lesson.get("duration_minutes", 60)),
                        "max_slots": max_slots,
                        "free_slots": max(0, max_slots - taken_slots)
                    })

                if parsed_lessons:
                    parsed_days.append({
                        "key": day_key,
                        "title": day_title,
                        "lessons": parsed_lessons
                    })

            parsed_disciplines.append({
                "code": disc_key,
                "name": disc_name,
                "days": parsed_days
            })

    # РћС‚РґР°РµРј С‡РёСЃС‚С‹Р№ РєРѕРЅС‚РµРєСЃС‚ РІ С€Р°Р±Р»РѕРЅ
    ui = settings.get("ui", {}) if isinstance(settings.get("ui", {}), dict) else {}
    loading = ui.get("loading", {}) if isinstance(ui.get("loading", {}), dict) else {}
    context = {
        "request": request,
        "club_id": club_id,
        "club_name": club.name or 'Р‘РµР· РЅР°Р·РІР°РЅРёСЏ',
        "disciplines": parsed_disciplines
        ,"loading": {"enabled": bool(loading.get("enabled", False)),
                     "duration_ms": max(300, min(10000, int(loading.get("duration_ms", 1200)))),
                     "message": str(loading.get("message", "Р—Р°РіСЂСѓР¶Р°РµРј РїСЂРёР»РѕР¶РµРЅРёРµвЂ¦"))},
        "logo_url": str(ui.get("logo_url", ""))
    }
    return templates.TemplateResponse("schedule.html", context)


@router.get("/privacy", response_class=HTMLResponse)
async def get_privacy_page(request: Request, session: AsyncSession = Depends(get_session)):
    """РЎС‚СЂР°РЅРёС†Р° РїРѕР»РёС‚РёРєРё РєРѕРЅС„РёРґРµРЅС†РёР°Р»СЊРЅРѕСЃС‚Рё РґР»СЏ WebApp"""
    club_id = get_club_id_from_host(request)
    club = (await session.execute(select(Club).where(Club.id == club_id))).scalar_one_or_none() if club_id else None
    if club and not legal_documents_enabled(club.club_settings):
        raise HTTPException(status_code=404, detail="Юридические документы отключены клубом")
    return templates.TemplateResponse("privacy.html", {"request": request, **legal_context(club)})


@router.get("/oferta", response_class=HTMLResponse)
async def get_oferta_page(request: Request, session: AsyncSession = Depends(get_session)):
    """РЎС‚СЂР°РЅРёС†Р° РїСѓР±Р»РёС‡РЅРѕР№ РѕС„РµСЂС‚С‹ РґР»СЏ WebApp"""
    club_id = get_club_id_from_host(request)
    club = (await session.execute(select(Club).where(Club.id == club_id))).scalar_one_or_none() if club_id else None
    if club and not legal_documents_enabled(club.club_settings):
        raise HTTPException(status_code=404, detail="Юридические документы отключены клубом")
    return templates.TemplateResponse("oferta.html", {"request": request, **legal_context(club)})


@router.get("/platform-oferta", response_class=HTMLResponse)
async def get_platform_oferta_page(request: Request):
    """Immutable SaaS license offer for club owners."""
    return templates.TemplateResponse("platform_oferta.html", {"request": request, **platform_legal_context()})


@router.get("/webapp/admin-legal", response_class=HTMLResponse)
async def admin_legal_page(request: Request, club_id: int = Query(...), init_data: str | None = Query(default=None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data:
        return telegram_init_gate('/webapp/admin-legal', club_id, 'Откройте юридические документы из Telegram')
    await verify_webapp_admin(club, init_data)
    settings = club.club_settings if isinstance(club.club_settings, dict) else {}
    legal = settings.get("legal", {}) if isinstance(settings.get("legal", {}), dict) else {}
    return templates.TemplateResponse("admin_legal.html", {"request": request, "club": club, "club_id": club_id, "legal": legal})


@router.post("/webapp/admin-legal/save")
async def save_admin_legal(request: Request, club_id: int = Query(...), session: AsyncSession = Depends(get_session)):
    payload = await request.json()
    club = await session.get(Club, club_id)
    tg_user = await verify_webapp_admin(club, payload.get("init_data"))
    club = (await session.execute(select(Club).where(Club.id == club_id).with_for_update())).scalar_one()
    if not isinstance(payload.get("enabled", True), bool):
        raise HTTPException(status_code=400, detail="Поле публикации должно быть логическим")
    fields = ("provider_name", "provider_type", "inn", "ogrn", "legal_address", "club_address", "email", "phone", "document_version", "privacy_operator")
    allowed = {"init_data", "enabled", *fields}
    if set(payload) - allowed:
        raise HTTPException(status_code=400, detail="Передано неизвестное поле юридических документов")
    settings = dict(club.club_settings or {})
    fields = ("provider_name", "provider_type", "inn", "ogrn", "legal_address", "club_address", "email", "phone", "document_version", "updated_at", "privacy_operator")
    legal = {field: str(payload.get(field, "") or "").strip()[:500] for field in fields}
    provider_types = {"ип": "ИП", "ооо": "ООО", "самозанятый": "Самозанятый", "физическое лицо": "Физическое лицо"}
    legal["provider_type"] = provider_types.get(legal["provider_type"].casefold(), legal["provider_type"])
    legal["document_version"] = legal["document_version"] or "1.0"
    legal["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    legal["enabled"] = bool(payload.get("enabled", True))
    # platform_name belongs to the immutable SaaS offer, never to the club form
    legal.pop("platform_name", None)
    if legal["enabled"]:
        errors = validate_legal_data(legal)
        if errors:
            raise HTTPException(status_code=400, detail="; ".join(errors))
    settings["legal"] = legal
    club.club_settings = settings
    await session.commit()
    audit_event("legal_documents_updated", club_id=club.id, actor_user_id=int(tg_user.get("id", 0)), action="update", object_type="legal_documents", location="webapp/admin-legal")
    return {"ok": True, "legal": legal}


@router.post("/webapp/admin-legal/delete")
async def delete_admin_legal(request: Request, club_id: int = Query(...), session: AsyncSession = Depends(get_session)):
    payload = await request.json()
    club = await session.get(Club, club_id)
    tg_user = await verify_webapp_admin(club, payload.get("init_data"))
    settings = dict(club.club_settings or {})
    settings.pop("legal", None)
    settings["legal"] = {"enabled": False}
    club.club_settings = settings
    await session.commit()
    audit_event("legal_documents_deleted", club_id=club.id, actor_user_id=int(tg_user.get("id", 0)), action="delete", object_type="legal_documents", location="webapp/admin-legal")
    return {"ok": True}


@router.get("/webapp/live_cam", response_class=HTMLResponse)
async def get_cameras_page(request: Request, club_id: int = Query(...), init_data: str | None = Query(default=None), session: AsyncSession = Depends(get_session)):
    club = await session.get(Club, club_id)
    if not init_data:
        return webapp_auth_gate(request, club_id)
    await verify_webapp_admin(club, init_data)
    return templates.TemplateResponse("cameras.html", {"request": request, "club_id": club_id})


# 2. Р РѕСѓС‚ РіРµРЅРµСЂР°С†РёРё СЃС‚СЂРёРјР°
@router.get("/webapp/live_cam/stream")
async def video_stream(
        club_id: int = Query(...),
        camera_src: str | None = None,
        init_data: str | None = Query(default=None),
        session: AsyncSession = Depends(get_session)
):
    """
    РџСЂРѕРєСЃРёСЂСѓРµС‚ MJPEG РІРёРґРµРѕРїРѕС‚РѕРє РёР· РІРЅСѓС‚СЂРµРЅРЅРµРіРѕ РєРѕРЅС‚РµР№РЅРµСЂР° Docker (go2rtc)
    РЅР°РїСЂСЏРјСѓСЋ РІ WebApp СЃРјР°СЂС‚С„РѕРЅР°, РґРёРЅР°РјРёС‡РµСЃРєРё РїРѕРґСЃС‚Р°РІР»СЏСЏ РєР°РјРµСЂСѓ РёР· РЅР°СЃС‚СЂРѕРµРє РєР»СѓР±Р°.
    """
    # Р’С‹С‚Р°СЃРєРёРІР°РµРј РЅР°СЃС‚СЂРѕР№РєРё РёРјРµРЅРЅРѕ СЌС‚РѕРіРѕ РєР»СѓР±Р° РёР· Р‘Р” РґР»СЏ РёР·РѕР»СЏС†РёРё SaaS
    result = await session.execute(select(Club).where(Club.id == club_id))
    club = result.scalar_one_or_none()

    if not club:
        raise HTTPException(status_code=404, detail="РљР»СѓР± РЅРµ РЅР°Р№РґРµРЅ")
    if isinstance(init_data, str) or init_data is None:
        await verify_webapp_admin(club, init_data)

    # Р‘РµСЂРµРј РёРјСЏ РєР°РјРµСЂС‹ РёР· club_settings. Р•СЃР»Рё С‚Р°Рј РїСѓСЃС‚Рѕ, СЃС‚Р°РІРёРј РґРµС„РѕР»С‚РЅРѕРµ "camera1"
    settings = club.club_settings or {}
    turnstile_settings = settings.get("turnstile", {})
    if not isinstance(turnstile_settings, dict):
        turnstile_settings = {}
    camera_src = camera_src or turnstile_settings.get("camera_src") or "camera1"

    # РЎРјР°СЂС‚С„РѕРЅ РЅРµ РІРёРґРёС‚ РІРЅСѓС‚СЂРµРЅРЅСЋСЋ Docker-СЃРµС‚СЊ. РџРѕСЌС‚РѕРјСѓ FastAPI СЃР°Рј РїРѕРґРєР»СЋС‡Р°РµС‚СЃСЏ
    # Рє go2rtc Рё РїРµСЂРµРґР°РµС‚ РїРѕР»СѓС‡РµРЅРЅС‹Рµ Р±Р°Р№С‚С‹ РЅР°СЂСѓР¶Сѓ Р±РµР· РёР·РјРµРЅРµРЅРёСЏ.
    # go2rtc СЂР°Р±РѕС‚Р°РµС‚ РІ С‚РѕР№ Р¶Рµ Docker-СЃРµС‚Рё, С‡С‚Рѕ Рё API. РќРµ РёСЃРїРѕР»СЊР·СѓРµРј
    # host.docker.internal: СЌС‚Рѕ Р»РѕРјР°РµС‚СЃСЏ РЅР° Docker Desktop Рё РїСЂРё РґРµРїР»РѕРµ РЅР° Linux.
    go2rtc_mjpeg_api = "http://go2rtc:1984/api/stream.mjpeg"
    # РќРµРєРѕС‚РѕСЂС‹Рµ RTSP-РёСЃС‚РѕС‡РЅРёРєРё РїРѕРґРЅРёРјР°СЋС‚ РїРµСЂРІС‹Р№ РєР°РґСЂ Р·Р°РјРµС‚РЅРѕ РґРѕР»СЊС€Рµ РѕР±С‹С‡РЅРѕРіРѕ.
    # Р”Р°РµРј go2rtc Р±РѕР»СЊС€Рµ РІСЂРµРјРµРЅРё РёРјРµРЅРЅРѕ РЅР° СѓСЃС‚Р°РЅРѕРІР»РµРЅРёРµ СЃРѕРµРґРёРЅРµРЅРёСЏ СЃ РёСЃС‚РѕС‡РЅРёРєРѕРј.
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
            "РќРµ СѓРґР°Р»РѕСЃСЊ РїРѕРґРєР»СЋС‡РёС‚СЊСЃСЏ Рє go2rtc: club_id={}, camera_src={}, error={}",
            club_id,
            camera_src,
            exc
        )
        raise HTTPException(
            status_code=502,
            detail="РЎРµСЂРІРёСЃ РєР°РјРµСЂ РІСЂРµРјРµРЅРЅРѕ РЅРµРґРѕСЃС‚СѓРїРµРЅ"
        ) from exc

    if response.status_code != 200:
        status_code = response.status_code
        await response.aclose()
        await client.aclose()
        logger.error(
            "go2rtc РЅРµ РѕС‚РґР°Р» MJPEG-РїРѕС‚РѕРє: club_id={}, camera_src={}, status={}",
            club_id,
            camera_src,
            status_code
        )
        raise HTTPException(
            status_code=502,
            detail=f"РљР°РјРµСЂР° РЅРµ РѕС‚РґР°Р»Р° РІРёРґРµРѕРїРѕС‚РѕРє (go2rtc: {status_code})"
        )

    # Р’ MJPEG Р·Р°РіРѕР»РѕРІРѕРє Content-Type СЃРѕРґРµСЂР¶РёС‚ boundary вЂ” РёРјСЏ СЂР°Р·РґРµР»РёС‚РµР»СЏ РєР°РґСЂРѕРІ.
    # РќРµР»СЊР·СЏ РїРѕРґСЃС‚Р°РІР»СЏС‚СЊ РµРіРѕ РІСЂСѓС‡РЅСѓСЋ: Сѓ СЂР°Р·РЅС‹С… РёСЃС‚РѕС‡РЅРёРєРѕРІ РѕРЅРѕ РјРѕР¶РµС‚ РѕС‚Р»РёС‡Р°С‚СЊСЃСЏ.
    content_type = response.headers.get(
        "content-type",
        "multipart/x-mixed-replace; boundary=frame"
    )

    if "multipart/x-mixed-replace" not in content_type.lower():
        await response.aclose()
        await client.aclose()
        logger.error(
            "go2rtc РІРµСЂРЅСѓР» РЅРµРѕР¶РёРґР°РЅРЅС‹Р№ Content-Type: club_id={}, camera_src={}, content_type={}",
            club_id,
            camera_src,
            content_type
        )
        raise HTTPException(
            status_code=502,
            detail="РљР°РјРµСЂР° РґРѕСЃС‚СѓРїРЅР°, РЅРѕ РЅРµ РѕС‚РґР°РµС‚ MJPEG. РџСЂРѕРІРµСЂСЊС‚Рµ РєРѕРґРµРє РІ go2rtc"
        )

    async def stream_generator():
        try:
            # aiter_raw СЃРѕС…СЂР°РЅСЏРµС‚ multipart-СЂР°Р·РјРµС‚РєСѓ Рё РіСЂР°РЅРёС†С‹ JPEG-РєР°РґСЂРѕРІ РєР°Рє РµСЃС‚СЊ.
            async for chunk in response.aiter_raw():
                yield chunk
        except httpx.HTTPError as exc:
            logger.warning(
                "MJPEG-РїРѕС‚РѕРє РѕР±РѕСЂРІР°Р»СЃСЏ: club_id={}, camera_src={}, error={}",
                club_id,
                camera_src,
                exc
            )
        finally:
            await response.aclose()
            await client.aclose()

    # X-Accel-Buffering Р·Р°РїСЂРµС‰Р°РµС‚ Nginx РєРѕРїРёС‚СЊ РєР°РґСЂС‹ РІ Р±СѓС„РµСЂРµ.
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
    Р­РЅРґРїРѕРёРЅС‚, РєРѕС‚РѕСЂС‹Р№ РѕС‚РєСЂС‹РІР°РµС‚СЃСЏ РІ Telegram WebApp РїРѕ СЃСЃС‹Р»РєРµ:
    https://С‚РІРѕСЏ_РєСѓС‡Р°_РїРѕРґРґРѕРјРµРЅРѕРІ.ru/admin/pass-app?user_id={telegram_id}
    """
    if not init_data:
        return HTMLResponse(f"""<!doctype html><meta charset='utf-8'>
<script src='https://telegram.org/js/telegram-web-app.js'></script><script>
const tg=window.Telegram.WebApp; tg.ready();
if (!tg.initData) document.body.innerText='РћС‚РєСЂРѕР№С‚Рµ РїСЂРёР»РѕР¶РµРЅРёРµ РёР· Telegram';
else location.replace(location.pathname+'?user_id={user_id}&init_data='+encodeURIComponent(tg.initData));
</script>""", status_code=401)

    # Р’С‹С‚Р°СЃРєРёРІР°РµРј СЂРѕРґРёС‚РµР»СЏ Рё РїСЂРѕРІРµСЂСЏРµРј РїРѕРґРїРёСЃР°РЅРЅС‹Рµ РґР°РЅРЅС‹Рµ Telegram РґРѕ РІС‹РґР°С‡Рё РґРµС‚РµР№.
    query = select(User).where(User.user_id == user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ РЅРµ РЅР°Р№РґРµРЅ")

    students_for_club = await db.execute(select(Student.club_id).where(Student.parent_id == user_id, Student.club_id.isnot(None)).limit(1))
    club_id = students_for_club.scalar()
    club = await db.get(Club, club_id) if club_id else None
    tg_user = verify_telegram_data(init_data, club.bot_token if club else "")
    if not tg_user or int(tg_user.get("id", 0)) != user_id:
        raise HTTPException(status_code=403, detail="Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰РµРЅ")
    settings = (club.club_settings or {}) if club else {}
    ui = settings.get("ui", {}) if isinstance(settings.get("ui", {}), dict) else {}
    loading = ui.get("loading", {}) if isinstance(ui.get("loading", {}), dict) else {}
    freeze_price_per_day = settings.get("limits", {}).get("freeze_price_per_day", 0)
    # Р”РѕСЃС‚Р°РµРј СЃРїРёСЃРѕРє СЃС‚СѓРґРµРЅС‚РѕРІ РґР»СЏ СЌС‚РѕРіРѕ СЂРѕРґРёС‚РµР»СЏ
    students_query = select(Student).where(Student.parent_id == user_id, Student.club_id == club.id)
    students_result = await db.execute(students_query)
    students = students_result.scalars().all()

    # Р РµРЅРґРµСЂРёРј HTML СЃС‚СЂР°РЅРёС†Сѓ Рё РїРµСЂРµРґР°РµРј С‚СѓРґР° СЃРїРёСЃРѕРє РґРµС‚РµР№
    return templates.TemplateResponse("biometric_pass.html", {"request": request, "students": students,
        "club_id": club_id, "user_id": user_id, "club_name": club.name if club else "", "logo_url": ui.get("logo_url", ""),
        "loading": {"enabled": bool(loading.get("enabled", False)), "duration_ms": max(300, min(10000, int(loading.get("duration_ms", 1200)))), "message": str(loading.get("message", "Р—Р°РіСЂСѓР¶Р°РµРј РїСЂРёР»РѕР¶РµРЅРёРµвЂ¦"))}})
