import uuid
from datetime import datetime, timedelta
from html import escape

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_module.router_base import router, templates
from admin_module.schemas import (
    BiometricCheckIn,
    WebAppActionPayload,
    WebAppBindPhonePayload,
    WebAppCreateStudentPayload,
    WebAppBuySubscriptionPayload,
)
from admin_module.webapp_verify import verify_telegram_data
from config import PROXY_URL
from database.db import Club, PaymentOrder, CartOrder, CartItem, Student, Subscription, User, VisitLog, get_session, process_student_freeze
from services.audit import audit_event
from services.abuse_guard import rate_limit, audit_block
from services.yookassa_client import YooKassaClient
from services.input_normalization import normalize_ru_phone
from services.visit_history import attach_student_names, group_completed_sessions, summarize_payment_entry
from aiogram import Bot


def _auth_gate_html(target: str, **params):
    qs = "&".join(
        f"{k}={v}"
        for k, v in params.items()
        if v is not None and v != ""
    )
    return HTMLResponse(f"""<!doctype html><meta charset='utf-8'>
<script src='https://telegram.org/js/telegram-web-app.js'></script><script>
const tg=window.Telegram.WebApp; tg.ready();
if (!tg.initData) document.body.innerText='Откройте приложение из Telegram';
else location.replace(location.pathname+'?{qs}&init_data='+encodeURIComponent(tg.initData));
    </script>""", status_code=401)


def _absolute_webapp_url(request: Request, value: str | None) -> str:
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if value.startswith("/"):
        return str(request.base_url).rstrip("/") + value
    return value


def _webapp_loading_config(club) -> dict:
    settings = (club.club_settings or {}) if club else {}
    ui = settings.get("ui", {}) if isinstance(settings.get("ui", {}), dict) else {}
    loading = ui.get("loading", {}) if isinstance(ui.get("loading", {}), dict) else {}
    return {
        "enabled": bool(loading.get("enabled", False)),
        "duration_ms": max(300, min(10000, int(loading.get("duration_ms", 1200)))),
        "message": str(loading.get("message", "Загружаем приложение…")),
        "logo_url": ui.get("logo_url", ""),
    }


def _normalize_payment_method(value: str | None) -> str:
    method = (value or "bank_card").strip().lower()
    if method in {"sbp", "yookassa_sbp"}:
        return "sbp"
    return "bank_card"


def _default_student_discipline(club_settings: dict) -> str:
    disciplines = club_settings.get("disciplines", {}) if isinstance(club_settings, dict) else {}
    for code, cfg in disciplines.items():
        if isinstance(cfg, dict) and cfg.get("active"):
            return str(code)
    return "boxing"


async def _ensure_webapp_user_linked(db: AsyncSession, user_id: int, club_id: int) -> User | None:
    user = await db.get(User, user_id)
    if not user:
        return None
    if user.club_id != club_id:
        has_students = await db.scalar(
            select(Student.id).where(Student.parent_id == user_id, Student.club_id == club_id).limit(1)
        )
        if has_students:
            user.club_id = club_id
            await db.commit()
        else:
            return None
    return user


async def _get_saved_subscription(db: AsyncSession, club_id: int, user_id: int) -> Subscription | None:
    result = await db.execute(
        select(Subscription)
        .where(
            Subscription.club_id == club_id,
            Subscription.user_id == user_id,
            Subscription.rebill_id.is_not(None),
        )
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


@router.get("/webapp/biometric-pass", response_class=HTMLResponse)
async def get_biometric_page(
    request: Request,
    club_id: int,
    user_id: int,
    init_data: str | None = Query(default=None),
    db: AsyncSession = Depends(get_session),
):
    if not init_data:
        return _auth_gate_html("webapp/biometric-pass", club_id=club_id, user_id=user_id)
    club = await db.get(Club, club_id)
    tg_user = verify_telegram_data(init_data, club.bot_token if club else "")
    if not tg_user or int(tg_user.get("id", 0)) != user_id:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    students = (await db.execute(select(Student).where(Student.parent_id == user_id, Student.club_id == club_id))).scalars().all()
    linked_user = await db.get(User, user_id)
    settings = (club.club_settings or {}) if club else {}
    ui = settings.get("ui", {}) if isinstance(settings.get("ui", {}), dict) else {}
    loading = ui.get("loading", {}) if isinstance(ui.get("loading", {}), dict) else {}
    return templates.TemplateResponse("biometric_pass.html", {"request": request, "students": students, "club": club, "club_id": club_id, "user_id": user_id, "biometric_enabled": bool(getattr(linked_user, "is_biometric_enabled", False)), "club_name": club.name if club else "", "logo_url": _absolute_webapp_url(request, ui.get("logo_url", "")), "loading": {"enabled": bool(loading.get("enabled", False)), "duration_ms": max(300, min(10000, int(loading.get("duration_ms", 1200)))), "message": str(loading.get("message", "Загружаем приложение…"))}})


@router.get("/webapp/client-cabinet", response_class=HTMLResponse)
async def get_client_cabinet_page(request: Request, club_id: int, init_data: str | None = Query(default=None), db: AsyncSession = Depends(get_session)):
    if not init_data:
        return _auth_gate_html("webapp/client-cabinet", club_id=club_id)
    club = await db.get(Club, club_id)
    if not club or not club.bot_token:
        raise HTTPException(status_code=404, detail="Клуб не найден")
    tg_user = verify_telegram_data(init_data, club.bot_token)
    if not tg_user:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    user_id = int(tg_user.get("id", 0))
    user = await _ensure_webapp_user_linked(db, user_id, club_id)
    if not user:
        return await webapp_auth_help_page(request=request, club_id=club_id, init_data=init_data, db=db)
    settings = (club.club_settings or {}) if club else {}
    students = (await db.execute(select(Student).where(Student.parent_id == user_id, Student.club_id == club_id).order_by(Student.name))).scalars().all()
    active_students = sum(1 for s in students if not s.is_frozen and s.expire_date and s.expire_date > datetime.now())
    frozen_students = sum(1 for s in students if s.is_frozen)
    expired_students = sum(1 for s in students if not s.is_frozen and (not s.expire_date or s.expire_date <= datetime.now()))
    audit_event("webapp_cabinet_opened", club_id=club_id, user_id=user_id, students=len(students))
    loading = _webapp_loading_config(club)
    return templates.TemplateResponse(
        "client_cabinet.html",
        {
            "request": request,
            "club": club,
            "club_id": club_id,
            "user_id": user_id,
            "club_name": club.name if club else "",
            "logo_url": _absolute_webapp_url(request, loading["logo_url"]),
            "students": students,
            "user_name": user.full_name or tg_user.get("first_name", ""),
            "now": datetime.now(),
            "summary": {
                "total": len(students),
                "active": active_students,
                "frozen": frozen_students,
                "expired": expired_students,
                "free_freeze_available": any((s.can_freeze or 0) > 0 for s in students),
            },
            "loading": {
                "enabled": loading["enabled"],
                "duration_ms": loading["duration_ms"],
                "message": loading["message"],
            },
            "freeze_price_per_day": settings.get("limits", {}).get("freeze_price_per_day", 0),
        },
    )


@router.get("/webapp/client-cabinet/create-student", response_class=HTMLResponse)
async def webapp_create_student_page(request: Request, club_id: int, init_data: str | None = Query(default=None), db: AsyncSession = Depends(get_session)):
    if not init_data:
        return _auth_gate_html("webapp/client-cabinet/create-student", club_id=club_id)
    club = await db.get(Club, club_id)
    if not club or not club.bot_token:
        raise HTTPException(status_code=404, detail="РљР»СѓР± РЅРµ РЅР°Р№РґРµРЅ")
    tg_user = verify_telegram_data(init_data, club.bot_token)
    if not tg_user:
        raise HTTPException(status_code=403, detail="Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰РµРЅ")
    user = await _ensure_webapp_user_linked(db, int(tg_user.get("id", 0)), club_id)
    if not user:
        return await webapp_auth_help_page(request=request, club_id=club_id, init_data=init_data, db=db)
    settings = club.club_settings or {}
    return templates.TemplateResponse(
        "webapp_create_student.html",
        {
            "request": request,
            "club": club,
            "club_id": club_id,
            "default_discipline": _default_student_discipline(settings),
            "disciplines": settings.get("disciplines", {}),
        },
    )


@router.post("/webapp/client-cabinet/create-student")
async def webapp_create_student_submit(payload: WebAppCreateStudentPayload, db: AsyncSession = Depends(get_session)):
    club = await db.get(Club, payload.club_id)
    if not club or not club.bot_token:
        raise HTTPException(status_code=404, detail="РљР»СѓР± РЅРµ РЅР°Р№РґРµРЅ")
    tg_user = verify_telegram_data(payload.init_data, club.bot_token)
    if not tg_user:
        raise HTTPException(status_code=403, detail="Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰РµРЅ")
    user_id = int(tg_user.get("id", 0))
    user = await _ensure_webapp_user_linked(db, user_id, payload.club_id)
    if not user:
        raise HTTPException(status_code=403, detail="РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ РЅРµ РїСЂРёРІСЏР·Р°РЅ Рє РєР»СѓР±Сѓ")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Р’РІРµРґРёС‚Рµ РёРјСЏ Р°С‚Р»РµС‚Р°")
    birthday = None
    if payload.birthday:
        from services.input_normalization import parse_user_date
        try:
            birthday = parse_user_date(payload.birthday)
        except ValueError:
            raise HTTPException(status_code=400, detail="РќРµРєРѕСЂСЂРµРєС‚РЅР°СЏ РґР°С‚Р° СЂРѕР¶РґРµРЅРёСЏ")
    existing = (await db.execute(select(Student).where(Student.club_id == club.id, Student.parent_id == user_id))).scalars().all()
    duplicate = next((student for student in existing if student.name.strip().casefold() == name.casefold() and student.birthday == birthday), None)
    if duplicate:
        raise HTTPException(status_code=409, detail="РўР°РєРѕР№ Р°С‚Р»РµС‚ СѓР¶Рµ РµСЃС‚СЊ")
    student = Student(
        club_id=club.id,
        parent_id=user_id,
        name=name,
        birthday=birthday,
        parent_phone=normalize_ru_phone(payload.phone) if payload.phone else None,
        discipline=_default_student_discipline(club.club_settings or {}),
        balance_lessons=0,
        expire_date=None,
        can_freeze=1,
        is_frozen=0,
    )
    db.add(student)
    await db.commit()
    audit_event("webapp_student_created", club_id=club.id, user_id=user_id, student_id=student.id, student_name=student.name)
    return {"ok": True, "student": {"id": student.id, "name": student.name}}


@router.get("/webapp/client-cabinet/freeze", response_class=HTMLResponse)
async def webapp_freeze_page(request: Request, club_id: int, student_id: int, init_data: str | None = Query(default=None), db: AsyncSession = Depends(get_session)):
    if not init_data:
        return _auth_gate_html("webapp/client-cabinet/freeze", club_id=club_id, student_id=student_id)
    club = await db.get(Club, club_id)
    if not club or not club.bot_token:
        raise HTTPException(status_code=404, detail="Клуб не найден")
    tg_user = verify_telegram_data(init_data, club.bot_token)
    if not tg_user:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    student = await db.get(Student, student_id)
    if not student or student.club_id != club_id or student.parent_id != int(tg_user.get("id", 0)):
        raise HTTPException(status_code=403, detail="Атлет не найден")
    freeze_days = (club.club_settings or {}).get("limits", {}).get("freeze_days_step", 7)
    now = datetime.now()
    can_freeze = bool(student.expire_date and student.expire_date > now and getattr(student, "can_freeze", 0) > 0 and not getattr(student, "is_frozen", 0))
    return templates.TemplateResponse("webapp_freeze.html", {"request": request, "club": club, "student": student, "club_id": club_id, "freeze_days": freeze_days, "can_freeze": can_freeze})


@router.post("/webapp/client-cabinet/freeze")
async def webapp_freeze_submit(payload: WebAppActionPayload, db: AsyncSession = Depends(get_session)):
    """Apply a free freeze from the client WebApp."""
    club = await db.get(Club, payload.club_id)
    if not club or not club.bot_token:
        raise HTTPException(status_code=404, detail="Клуб не найден")
    tg_user = verify_telegram_data(payload.init_data, club.bot_token)
    if not tg_user:
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    user_id = int(tg_user.get("id", 0))
    student = await db.get(Student, payload.student_id, with_for_update=True)
    if not student or student.club_id != club.id or student.parent_id != user_id:
        raise HTTPException(status_code=403, detail="Атлет не найден")

    freeze_days = int((club.club_settings or {}).get("limits", {}).get("freeze_days_step", 7))
    new_expire = await process_student_freeze(
        student_id=student.id,
        club_id=club.id,
        club_settings=club.club_settings or {},
        session=db,
        days=freeze_days,
    )
    if new_expire == "disabled":
        raise HTTPException(status_code=400, detail="Заморозка отключена в настройках клуба")
    if not new_expire:
        raise HTTPException(status_code=409, detail="Заморозка недоступна: проверьте абонемент и лимит")

    audit_event("webapp_freeze_applied", club_id=club.id, user_id=user_id, student_id=student.id, days=freeze_days)
    if club.owner_id and club.owner_id != user_id:
        bot = Bot(club.bot_token)
        try:
            await bot.send_message(
                club.owner_id,
                (
                    "❄️ <b>Клиент заморозил абонемент через WebApp</b>\n\n"
                    f"Атлет: <b>{escape(student.name)}</b>\n"
                    f"Клиент ID: <code>{user_id}</code>\n"
                    f"Срок: <b>{freeze_days} дн.</b>\n"
                    f"Новая дата окончания: <b>{new_expire.strftime('%d.%m.%Y')}</b>"
                ),
                parse_mode="HTML",
            )
        except Exception:
            # Уведомление не должно откатывать уже сохраненную заморозку.
            from loguru import logger
            logger.exception("Не удалось уведомить владельца о WebApp-заморозке")
        finally:
            await bot.session.close()
    return {"ok": True, "new_expire": new_expire.strftime("%d.%m.%Y")}


@router.get("/webapp/client-cabinet/student", response_class=HTMLResponse)
async def webapp_student_page(request: Request, club_id: int, student_id: int, init_data: str | None = Query(default=None), db: AsyncSession = Depends(get_session)):
    if not init_data:
        return _auth_gate_html("webapp/client-cabinet/student", club_id=club_id, student_id=student_id)
    club = await db.get(Club, club_id)
    if not club or not club.bot_token:
        raise HTTPException(status_code=404, detail="Клуб не найден")
    tg_user = verify_telegram_data(init_data, club.bot_token)
    if not tg_user:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    student = await db.get(Student, student_id)
    if not student or student.club_id != club_id or student.parent_id != int(tg_user.get("id", 0)):
        raise HTTPException(status_code=403, detail="Атлет не найден")
    visits = (await db.execute(
        select(VisitLog)
        .where(VisitLog.club_id == club_id, VisitLog.student_id == student_id)
        .order_by(VisitLog.visited_at.asc())
    )).scalars().all()
    visit_sessions = group_completed_sessions(
        attach_student_names(visits, {student.id: student.name}),
        int((club.club_settings or {}).get("limits", {}).get("session_timeout_minutes", 150)),
    )
    freeze_price_per_day = (club.club_settings or {}).get("limits", {}).get("freeze_price_per_day", 0)
    return templates.TemplateResponse("webapp_student.html", {"request": request, "club": club, "student": student, "club_id": club_id, "freeze_price_per_day": freeze_price_per_day, "visit_sessions": visit_sessions, "now": datetime.now()})


@router.get("/webapp/client-cabinet/history", response_class=HTMLResponse)
async def webapp_history_page(request: Request, club_id: int, student_id: int | None = Query(default=None), init_data: str | None = Query(default=None), db: AsyncSession = Depends(get_session)):
    if not init_data:
        return _auth_gate_html("webapp/client-cabinet/history", club_id=club_id, student_id=student_id or "")
    club = await db.get(Club, club_id)
    if not club or not club.bot_token:
        raise HTTPException(status_code=404, detail="Клуб не найден")
    tg_user = verify_telegram_data(init_data, club.bot_token)
    if not tg_user:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    user_id = int(tg_user.get("id", 0))
    user = await _ensure_webapp_user_linked(db, user_id, club_id)
    if not user:
        return await webapp_auth_help_page(request=request, club_id=club_id, init_data=init_data, db=db)
    students = (await db.execute(select(Student).where(Student.parent_id == user_id, Student.club_id == club_id).order_by(Student.name))).scalars().all()
    student_ids = [student.id for student in students]
    if student_id:
        student_ids = [student_id] if student_id in student_ids else []
    orders = (await db.execute(select(PaymentOrder).where(PaymentOrder.club_id == club_id, PaymentOrder.student_id.in_(student_ids)).order_by(PaymentOrder.created_at.desc()).limit(20))).scalars().all()
    cart_orders = (await db.execute(select(CartOrder).where(CartOrder.club_id == club_id, CartOrder.user_id == user_id).order_by(CartOrder.created_at.desc()).limit(10))).scalars().all()
    cart_items_by_order = {}
    if cart_orders:
        cart_ids = [cart.id for cart in cart_orders]
        cart_items = (await db.execute(select(CartItem).where(CartItem.cart_order_id.in_(cart_ids)))).scalars().all()
        for item in cart_items:
            cart_items_by_order.setdefault(item.cart_order_id, []).append(item.title)
    subscriptions = (await db.execute(select(Subscription).where(Subscription.club_id == club_id, Subscription.user_id == user_id).order_by(Subscription.created_at.desc()))).scalars().all()

    visit_rows = (await db.execute(
        select(VisitLog)
        .where(VisitLog.club_id == club_id, VisitLog.student_id.in_(student_ids))
        .order_by(VisitLog.visited_at.asc())
    )).scalars().all()
    visit_sessions = group_completed_sessions(
        attach_student_names(visit_rows, {student.id: student.name for student in students}),
        int((club.club_settings or {}).get("limits", {}).get("session_timeout_minutes", 150)),
    )

    payment_lines = []
    for order in orders:
        student_name = next((s.name for s in students if s.id == order.student_id), None)
        payment_lines.append(summarize_payment_entry(order, student_name=student_name))
    for cart in cart_orders:
        payment_lines.append(summarize_payment_entry(cart, item_titles=cart_items_by_order.get(cart.id, [])))

    subscription_lines = [
        f"• {sub.created_at.strftime('%d.%m.%Y %H:%M') if sub.created_at else '—'} — {'активна' if sub.is_active else 'неактивна'}"
        for sub in subscriptions
    ]

    return templates.TemplateResponse(
        "webapp_history.html",
        {
            "request": request,
            "club": club,
            "club_id": club_id,
            "payment_lines": payment_lines,
            "subscription_lines": subscription_lines,
            "visit_sessions": visit_sessions,
            "student_id": student_id,
        },
    )


@router.get("/webapp/client-cabinet/buy-subscription", response_class=HTMLResponse)
async def webapp_buy_subscription_page(request: Request, club_id: int, init_data: str | None = Query(default=None), db: AsyncSession = Depends(get_session)):
    if not init_data:
        return _auth_gate_html("webapp/client-cabinet/buy-subscription", club_id=club_id)
    club = await db.get(Club, club_id)
    if not club or not club.bot_token:
        raise HTTPException(status_code=404, detail="Клуб не найден")
    tg_user = verify_telegram_data(init_data, club.bot_token)
    if not tg_user:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    user = await _ensure_webapp_user_linked(db, int(tg_user.get("id", 0)), club_id)
    if not user:
        return await webapp_auth_help_page(request=request, club_id=club_id, init_data=init_data, db=db)
    students = (await db.execute(select(Student).where(Student.parent_id == user.user_id, Student.club_id == club_id).order_by(Student.name))).scalars().all()
    settings = club.club_settings or {}
    disciplines = settings.get("disciplines", {})
    sbp_enabled = bool(settings.get("payments", {}).get("yookassa_sbp_enabled", True))
    return templates.TemplateResponse("webapp_buy_subscription.html", {"request": request, "club": club, "club_id": club_id, "students": students, "disciplines": disciplines, "sbp_enabled": sbp_enabled})


@router.post("/webapp/client-cabinet/buy-subscription")
async def webapp_buy_subscription_submit(payload: WebAppBuySubscriptionPayload, request: Request, db: AsyncSession = Depends(get_session)):
    club = await db.get(Club, payload.club_id)
    if not club or not club.bot_token:
        raise HTTPException(status_code=404, detail="Клуб не найден")
    tg_user = verify_telegram_data(payload.init_data, club.bot_token)
    if not tg_user:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    user_id = int(tg_user.get("id", 0))
    user = await _ensure_webapp_user_linked(db, user_id, payload.club_id)
    if not user:
        raise HTTPException(status_code=403, detail="Пользователь не привязан к клубу")
    student = await db.get(Student, payload.student_id, with_for_update=True)
    if not student or student.club_id != payload.club_id or student.parent_id != user_id:
        raise HTTPException(status_code=403, detail="Атлет не найден")
    discipline_cfg = (club.club_settings or {}).get("disciplines", {}).get(payload.sport_type)
    if not discipline_cfg:
        raise HTTPException(status_code=400, detail="Направление недоступно")
    tariffs = discipline_cfg.get("tariffs", [])
    if payload.tariff_idx < 0 or payload.tariff_idx >= len(tariffs):
        raise HTTPException(status_code=400, detail="Тариф недоступен")
    selected_tariff = tariffs[payload.tariff_idx]
    price = selected_tariff.get("price")
    days = selected_tariff.get("days", 30)
    count = selected_tariff.get("count", 0)
    amount_kopecks = int(float(price) * 100)
    payment_method = _normalize_payment_method(payload.payment_method)
    pay_settings = (club.club_settings or {}).get("payments", {})
    shop_id = pay_settings.get("yookassa_shop_id")
    secret_key = pay_settings.get("yookassa_secret_key")
    if not shop_id or not secret_key:
        raise HTTPException(status_code=400, detail="ЮKassa не настроена")
    if payment_method == "sbp" and not bool(pay_settings.get("yookassa_sbp_enabled", True)):
        raise HTTPException(status_code=400, detail="СБП отключена в настройках клуба")
    redis = request.app.state.redis_client
    idem_key = f"idem:webapp:buy_sub:{club.id}:{user_id}:{student.id}:{payload.sport_type}:{payload.tariff_idx}"
    if not await rate_limit(redis, idem_key, 1, 90):
        await audit_block("webapp_checkout_blocked", "duplicate_subscription_checkout", club_id=club.id, user_id=user_id, student_id=student.id)
        raise HTTPException(status_code=409, detail="Платеж уже создается")
    active_pending = (
        await db.execute(
            select(PaymentOrder).where(
                PaymentOrder.club_id == club.id,
                PaymentOrder.student_id == student.id,
                PaymentOrder.type == "FIRST",
                PaymentOrder.status == "NEW",
            ).order_by(PaymentOrder.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if active_pending:
        pending_age = datetime.utcnow() - (active_pending.created_at or datetime.utcnow())
        if pending_age <= timedelta(minutes=10):
            await audit_block("webapp_checkout_blocked", "subscription_pending_order", club_id=club.id, user_id=user_id, student_id=student.id)
            raise HTTPException(status_code=409, detail="Для этого атлета уже создается платеж")
        active_pending.status = "FAILED"
        await db.commit()
    order_id = f"WEB_{uuid.uuid4().hex[:12].upper()}"
    order = PaymentOrder(id=order_id, user_id=user_id, student_id=student.id, club_id=club.id, amount_kopecks=amount_kopecks, lesson_count=count, days_to_add=days, discipline=payload.sport_type, status="NEW", type="FIRST")
    db.add(order)
    await db.commit()
    payment_data = None
    saved_subscription = await _get_saved_subscription(db, club.id, user_id)
    if payment_method == "bank_card" and saved_subscription and saved_subscription.rebill_id:
        payment_data = await YooKassaClient(shop_id=shop_id, secret_key=secret_key, proxy_url=PROXY_URL).charge_payment(
            order_id=order_id,
            amount_kopecks=amount_kopecks,
            payment_method_id=saved_subscription.rebill_id,
            club_name=club.name,
        )
        if payment_data.get("Success") and payment_data.get("Status") == "succeeded":
            order.status = "CONFIRMED"
            order.provider_payment_id = payment_data.get("PaymentId")
            next_charge_naive = (datetime.now() + timedelta(days=days))
            subscription = saved_subscription
            subscription.next_charge_at = next_charge_naive
            subscription.is_active = True
            subscription.amount_kopecks = amount_kopecks
            abon_result = await add_abon(
                student_id=student.id,
                lessons_count=count,
                session=db,
                club_id=club.id,
                club_settings=club.club_settings or {},
                days_to_add=days,
                discipline=payload.sport_type,
            )
            if abon_result:
                new_expire, _ = abon_result
                await db.commit()
                audit_event("webapp_subscription_checkout_created", club_id=club.id, user_id=user_id, student_id=student.id, amount_kopecks=amount_kopecks, sport_type=payload.sport_type, tariff_idx=payload.tariff_idx)
                return {"ok": True, "charged": True, "status": "succeeded", "message": f"Оплата прошла. Абонемент активирован до {new_expire}"}
        if payment_data.get("Success") and payment_data.get("Status") == "pending":
            order.provider_payment_id = payment_data.get("PaymentId")
            await db.commit()
            audit_event("webapp_subscription_checkout_created", club_id=club.id, user_id=user_id, student_id=student.id, amount_kopecks=amount_kopecks, sport_type=payload.sport_type, tariff_idx=payload.tariff_idx)
            return {"ok": True, "charged": False, "status": "pending", "message": "Оплата обрабатывается YooKassa"}

    payment_data = await YooKassaClient(shop_id=shop_id, secret_key=secret_key, proxy_url=PROXY_URL).init_payment(
        order_id=order_id,
        amount_kopecks=amount_kopecks,
        user_id=user_id,
        bot_username=club.bot_token,
        payment_method_type=payment_method,
    )
    if not payment_data.get("Success"):
        order.status = "FAILED"
        await db.commit()
        raise HTTPException(status_code=400, detail=payment_data.get("Message", "Ошибка создания платежа"))
    audit_event("webapp_subscription_checkout_created", club_id=club.id, user_id=user_id, student_id=student.id, amount_kopecks=amount_kopecks, sport_type=payload.sport_type, tariff_idx=payload.tariff_idx)
    return {"ok": True, "payment_url": payment_data["PaymentURL"], "charged": False}


@router.get("/webapp/client-cabinet/auth", response_class=HTMLResponse)
async def webapp_auth_help_page(request: Request, club_id: int, init_data: str | None = Query(default=None), db: AsyncSession = Depends(get_session)):
    if not init_data:
        return _auth_gate_html("webapp/client-cabinet/auth", club_id=club_id)
    club = await db.get(Club, club_id)
    if not club or not club.bot_token:
        raise HTTPException(status_code=404, detail="Клуб не найден")
    if not verify_telegram_data(init_data, club.bot_token):
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    return templates.TemplateResponse("webapp_bind_phone.html", {"request": request, "club": club, "club_id": club_id})


@router.post("/webapp/client-cabinet/auth")
async def webapp_bind_phone_submit(payload: WebAppBindPhonePayload, request: Request, db: AsyncSession = Depends(get_session)):
    club = await db.get(Club, payload.club_id)
    if not club or not club.bot_token:
        raise HTTPException(status_code=404, detail="Клуб не найден")
    tg_user = verify_telegram_data(payload.init_data, club.bot_token)
    if not tg_user:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    normalized_phone = normalize_ru_phone(payload.phone)
    raw_phone = normalized_phone or ""
    if len(raw_phone) < 10:
        raise HTTPException(status_code=400, detail="Введите номер телефона")
    clean_phone_10 = normalized_phone[-10:] if normalized_phone else ""
    redis = request.app.state.redis_client
    bind_key = f"rl:webapp:bind_phone:{club.id}:{tg_user.get('id', 0)}"
    if not await rate_limit(redis, bind_key, 3, 60):
        await audit_block("webapp_bind_blocked", "rate_limited", club_id=club.id, user_id=int(tg_user.get("id", 0)))
        raise HTTPException(status_code=429, detail="Слишком часто. Попробуйте позже.")
    students = (
        await db.execute(
            select(Student)
            .where(Student.parent_phone.contains(clean_phone_10), Student.club_id == club.id)
            .with_for_update()
        )
    ).scalars().all()
    if not students:
        raise HTTPException(status_code=404, detail="Атлеты с этим номером не найдены")
    user_id = int(tg_user.get("id", 0))
    user = await db.get(User, user_id, with_for_update=True)
    if not user:
        user = User(user_id=user_id, club_id=club.id, full_name=tg_user.get("first_name") or "", is_accepted=False, is_biometric_enabled=False)
        db.add(user)
        await db.flush()
    user.club_id = club.id
    for student in students:
        student.parent_id = user_id
        db.add(student)
    await db.commit()
    audit_event("webapp_phone_bound", club_id=club.id, user_id=user_id, students=[s.id for s in students], phone_tail=clean_phone_10[-4:])
    return {"ok": True, "message": f"Привязаны атлеты: {', '.join(s.name for s in students)}"}


@router.get("/webapp/client-cabinet/buy-freeze", response_class=HTMLResponse)
async def webapp_buy_freeze_page(request: Request, club_id: int, student_id: int, init_data: str | None = Query(default=None), db: AsyncSession = Depends(get_session)):
    if not init_data:
        return _auth_gate_html("webapp/client-cabinet/buy-freeze", club_id=club_id, student_id=student_id)
    club = await db.get(Club, club_id)
    if not club or not club.bot_token:
        raise HTTPException(status_code=404, detail="Клуб не найден")
    tg_user = verify_telegram_data(init_data, club.bot_token)
    if not tg_user:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    student = await db.get(Student, student_id)
    if not student or student.club_id != club_id or student.parent_id != int(tg_user.get("id", 0)):
        raise HTTPException(status_code=403, detail="Атлет не найден")
    settings = club.club_settings or {}
    price = settings.get("limits", {}).get("freeze_price_per_day", 0)
    sbp_enabled = bool(settings.get("payments", {}).get("yookassa_sbp_enabled", True))
    return templates.TemplateResponse("webapp_buy_freeze.html", {"request": request, "club": club, "student": student, "club_id": club_id, "price": price, "sbp_enabled": sbp_enabled})


@router.post("/webapp/client-cabinet/buy-freeze")
async def webapp_buy_freeze_submit(payload: WebAppActionPayload, request: Request, days: int, db: AsyncSession = Depends(get_session)):
    club = await db.get(Club, payload.club_id)
    if not club or not club.bot_token:
        raise HTTPException(status_code=404, detail="Клуб не найден")
    tg_user = verify_telegram_data(payload.init_data, club.bot_token)
    if not tg_user:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    student = await db.get(Student, payload.student_id, with_for_update=True)
    if not student or student.club_id != payload.club_id or student.parent_id != int(tg_user.get("id", 0)):
        raise HTTPException(status_code=403, detail="Атлет не найден")
    payment_method = _normalize_payment_method(payload.payment_method)
    price_per_day = float((club.club_settings or {}).get("limits", {}).get("freeze_price_per_day", 0))
    if price_per_day <= 0:
        raise HTTPException(status_code=400, detail="Покупка заморозки отключена")
    if not 1 <= days <= 365:
        raise HTTPException(status_code=400, detail="Неверное количество дней")
    amount_kopecks = int(round(price_per_day * days * 100))
    shop_id = getattr(club, "yookassa_shop_id", None) or (club.club_settings or {}).get("payments", {}).get("yookassa_shop_id")
    secret_key = getattr(club, "yookassa_secret_key", None) or (club.club_settings or {}).get("payments", {}).get("yookassa_secret_key")
    if not shop_id or not secret_key:
        raise HTTPException(status_code=400, detail="ЮKassa не настроена")
    if payment_method == "sbp" and not bool((club.club_settings or {}).get("payments", {}).get("yookassa_sbp_enabled", True)):
        raise HTTPException(status_code=400, detail="СБП отключена в настройках клуба")
    redis = request.app.state.redis_client
    idem_key = f"idem:webapp:buy_freeze:{club.id}:{int(tg_user.get('id', 0))}:{student.id}:{days}"
    if not await rate_limit(redis, idem_key, 1, 90):
        await audit_block("webapp_checkout_blocked", "duplicate_freeze_checkout", club_id=club.id, user_id=int(tg_user.get("id", 0)), student_id=student.id)
        raise HTTPException(status_code=409, detail="Платеж уже создается")
    active_pending = (
        await db.execute(
            select(PaymentOrder.id).where(
                PaymentOrder.club_id == club.id,
                PaymentOrder.student_id == student.id,
                PaymentOrder.type.like("FREEZE%"),
                PaymentOrder.status == "NEW",
            ).limit(1)
        )
    ).first()
    if active_pending:
        await audit_block("webapp_checkout_blocked", "freeze_pending_order", club_id=club.id, user_id=int(tg_user.get("id", 0)), student_id=student.id)
        raise HTTPException(status_code=409, detail="Для этой заморозки уже создается платеж")
    order_id = f"WEBFZ-{club.id}-{student.id}-{uuid.uuid4().hex[:12]}"
    order = PaymentOrder(
        id=order_id,
        user_id=int(tg_user.get("id", 0)),
        student_id=student.id,
        club_id=club.id,
        amount_kopecks=amount_kopecks,
        lesson_count=0,
        days_to_add=days,
        discipline="freeze",
        status="NEW",
        type=f"FREEZE_{days}",
    )
    db.add(order)
    await db.commit()
    payment_data = None
    saved_subscription = await _get_saved_subscription(db, club.id, int(tg_user.get("id", 0)))
    if payment_method == "bank_card" and saved_subscription and saved_subscription.rebill_id:
        payment_data = await YooKassaClient(shop_id=shop_id, secret_key=secret_key, proxy_url=PROXY_URL).charge_payment(
            order_id=order_id,
            amount_kopecks=amount_kopecks,
            payment_method_id=saved_subscription.rebill_id,
            club_name=club.name,
        )
        if payment_data.get("Success") and payment_data.get("Status") == "succeeded":
            order.status = "CONFIRMED"
            order.provider_payment_id = payment_data.get("PaymentId")
            new_expire = await process_student_freeze(
                student_id=student.id,
                club_id=club.id,
                club_settings=club.club_settings or {},
                session=db,
                days=days,
            )
            await db.commit()
            if new_expire and new_expire != "disabled":
                audit_event("webapp_freeze_checkout_created", club_id=club.id, user_id=int(tg_user.get("id", 0)), student_id=student.id, days=days, amount_kopecks=amount_kopecks)
                return {"ok": True, "charged": True, "status": "succeeded", "message": f"Оплата прошла. Заморозка активирована до {new_expire.strftime('%d.%m.%Y')}"}
        if payment_data.get("Success") and payment_data.get("Status") == "pending":
            order.provider_payment_id = payment_data.get("PaymentId")
            await db.commit()
            audit_event("webapp_freeze_checkout_created", club_id=club.id, user_id=int(tg_user.get("id", 0)), student_id=student.id, days=days, amount_kopecks=amount_kopecks)
            return {"ok": True, "charged": False, "status": "pending", "message": "Оплата обрабатывается YooKassa"}

    payment_data = await YooKassaClient(shop_id=shop_id, secret_key=secret_key, proxy_url=PROXY_URL).init_payment(
        order_id=order_id,
        amount_kopecks=amount_kopecks,
        user_id=int(tg_user.get("id", 0)),
        bot_username=club.bot_token,
        payment_method_type=payment_method,
    )
    if not payment_data.get("Success"):
        order.status = "FAILED"
        await db.commit()
        raise HTTPException(status_code=400, detail=payment_data.get("Message", "Ошибка создания платежа"))
    audit_event("webapp_freeze_checkout_created", club_id=club.id, user_id=int(tg_user.get("id", 0)), student_id=student.id, days=days, amount_kopecks=amount_kopecks)
    return {"ok": True, "payment_url": payment_data["PaymentURL"], "charged": False}
