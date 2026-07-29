import uuid
from datetime import datetime, timedelta
from html import escape

from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from admin_module.router_base import router, templates
from admin_module.schemas import (
    BiometricCheckIn,
    WebAppActionPayload,
    WebAppBindPhonePayload,
    WebAppCreateStudentPayload,
    WebAppBuySubscriptionPayload,
    AdminFreezePayload,
)
from admin_module.webapp_verify import verify_telegram_data
from admin_module.webapp_shared import verify_webapp_admin
from config import PROXY_URL
from database.db import Club, ClubStaff, PaymentOrder, CartOrder, CartItem, Student, StudentParent, Subscription, User, VisitLog, get_session, get_student_parent_ids, process_student_freeze
from services.audit import audit_event
from services.abuse_guard import rate_limit, audit_block
from services.yookassa_client import YooKassaClient
from services.input_normalization import normalize_ru_phone
from services.visit_history import attach_student_names, group_completed_sessions, summarize_payment_entry
from services.payment_requisites import get_payment_info_text, build_payment_instruction_text
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from handlers.skud import trigger_dingtian_turnstile
from services.gate_control import process_athlete_gate_pass
from services.staff_permissions import staff_can
from middlewares.db_saas_midleware import SUPER_ADMIN_IDS


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
    logo_url = str(loading.get("logo_url") or ui.get("logo_url") or "").strip()
    logo_rev = str(loading.get("logo_rev") or "").strip()
    return {
        "enabled": bool(loading.get("enabled", False)),
        "duration_ms": max(300, min(10000, int(loading.get("duration_ms", 1200)))),
        "message": str(loading.get("message", "Загружаем приложение…")),
        "logo_url": logo_url,
        "logo_rev": logo_rev,
    }


def _normalize_payment_method(value: str | None) -> str:
    method = (value or "bank_card").strip().lower()
    if method in {"sbp", "yookassa_sbp"}:
        return "sbp"
    if method in {"requisites", "manual", "cash", "requisite"}:
        return "requisites"
    return "bank_card"


def _student_age(student: Student) -> int | None:
    if not getattr(student, "birthday", None):
        return None
    today = datetime.now().date()
    return today.year - student.birthday.year - ((today.month, today.day) < (student.birthday.month, student.birthday.day))


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
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"manual_order_confirm_{order_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"manual_order_decline_{order_id}"),
        ]
    ])


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
    has_students = await db.scalar(
        select(Student.id).where(Student.parent_id == user_id, Student.club_id == club_id).limit(1)
    )
    if not has_students:
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


async def _is_staff_webapp_user(db: AsyncSession, club: Club, user_id: int) -> bool:
    if not club:
        return False
    if int(getattr(club, "owner_id", 0) or 0) == int(user_id):
        return True
    return bool(
        await db.scalar(
            select(ClubStaff.id).where(
                ClubStaff.club_id == club.id,
                ClubStaff.telegram_id == user_id,
                ClubStaff.is_active.is_(True),
            )
        )
    )


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
    loading_logo = _absolute_webapp_url(request, str(loading.get("logo_url") or ui.get("logo_url", "")))
    if loading_logo and loading.get("logo_rev"):
        loading_logo = f"{loading_logo}?v={loading['logo_rev']}"
    return templates.TemplateResponse("biometric_pass.html", {"request": request, "students": students, "club": club, "club_id": club_id, "user_id": user_id, "biometric_enabled": bool(getattr(linked_user, "is_biometric_enabled", False)), "club_name": club.name if club else "", "logo_url": loading_logo, "loading": {"enabled": bool(loading.get("enabled", False)), "duration_ms": max(300, min(10000, int(loading.get("duration_ms", 1200)))), "message": str(loading.get("message", "Загружаем приложение…"))}})


@router.get("/webapp/staff-pass", response_class=HTMLResponse)
async def get_staff_pass_page(request: Request, club_id: int, user_id: int | None = None, init_data: str | None = Query(default=None), db: AsyncSession = Depends(get_session)):
    if not init_data:
        return _auth_gate_html("webapp/staff-pass", club_id=club_id, user_id=user_id or "")
    club = await db.get(Club, club_id)
    if not club or not club.bot_token:
        raise HTTPException(status_code=404, detail="Клуб не найден")
    tg_user = verify_telegram_data(init_data, club.bot_token)
    resolved_user_id = int(user_id or tg_user.get("id", 0) if tg_user else 0)
    if not tg_user or int(tg_user.get("id", 0)) != resolved_user_id:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    is_owner = int(club.owner_id or 0) == resolved_user_id
    is_staff = bool(
        is_owner
        or (
            await db.scalar(
                select(ClubStaff.id).where(
                    ClubStaff.club_id == club_id,
                    ClubStaff.telegram_id == resolved_user_id,
                    ClubStaff.is_active.is_(True),
                )
            )
        )
    )
    if not is_staff:
        raise HTTPException(status_code=403, detail="Доступ только для сотрудников")
    settings = (club.club_settings or {}) if club else {}
    ui = settings.get("ui", {}) if isinstance(settings.get("ui", {}), dict) else {}
    loading = ui.get("loading", {}) if isinstance(ui.get("loading", {}), dict) else {}
    return templates.TemplateResponse(
        "staff_pass.html",
        {
            "request": request,
            "club": club,
            "club_id": club_id,
            "user_id": resolved_user_id,
            "is_admin": is_owner or resolved_user_id in {int(value) for value in SUPER_ADMIN_IDS},
            "club_name": club.name if club else "",
            "loading": {
                "enabled": bool(loading.get("enabled", False)),
                "duration_ms": max(300, min(10000, int(loading.get("duration_ms", 1200)))),
                "message": str(loading.get("message", "Загружаем приложение…")),
            },
        },
    )


@router.get("/webapp/admin-freeze", response_class=HTMLResponse)
async def admin_freeze_page(request: Request, club_id: int, init_data: str | None = Query(default=None), db: AsyncSession = Depends(get_session)):
    if not init_data:
        return _auth_gate_html("webapp/admin-freeze", club_id=club_id)
    club = await db.get(Club, club_id)
    await verify_webapp_admin(club, init_data)
    students = (await db.execute(select(Student).where(Student.club_id == club_id).order_by(Student.name))).scalars().all()
    freeze_days = int((club.club_settings or {}).get("limits", {}).get("freeze_days_step", 7))
    return templates.TemplateResponse("admin_freeze.html", {"request": request, "club": club, "club_id": club_id, "students": students, "freeze_days": freeze_days})


@router.post("/webapp/admin-freeze")
async def admin_freeze_submit(payload: AdminFreezePayload, db: AsyncSession = Depends(get_session)):
    club = await db.get(Club, payload.club_id)
    tg_user = await verify_webapp_admin(club, payload.init_data)
    student = await db.get(Student, payload.student_id, with_for_update=True)
    if not student or student.club_id != club.id:
        raise HTTPException(status_code=404, detail="Атлет не найден")
    action = payload.action.strip().casefold()
    settings = club.club_settings or {}
    if action == "freeze":
        days = int(settings.get("limits", {}).get("freeze_days_step", 7))
        result = await process_student_freeze(student.id, club.id, settings, db, days)
        if result == "disabled":
            raise HTTPException(status_code=400, detail="Заморозка отключена в настройках клуба")
        if not result:
            raise HTTPException(status_code=409, detail="Заморозка недоступна: проверьте абонемент, лимит или текущий статус")
        audit_event("admin_freeze_applied", club_id=club.id, actor_user_id=int(tg_user["id"]), student_id=student.id, days=days)
        return {"ok": True, "message": f"{student.name}: заморожен на {days} дней"}
    if action == "unfreeze":
        if not student.is_frozen:
            raise HTTPException(status_code=409, detail="Абонемент уже активен")
        now = datetime.utcnow()
        started = student.frozen_at.replace(tzinfo=None) if student.frozen_at else now
        freeze_days = int(student.frozen_days or settings.get("limits", {}).get("freeze_days_step", 7))
        elapsed = max(0, (now.date() - started.date()).days)
        returned_days = max(0, freeze_days - elapsed)
        if student.expire_date and returned_days:
            student.expire_date -= timedelta(days=returned_days)
        student.is_frozen = 0
        student.frozen_at = None
        student.frozen_days = None
        await db.commit()
        audit_event("admin_unfreeze_applied", club_id=club.id, actor_user_id=int(tg_user["id"]), student_id=student.id, returned_days=returned_days)
        return {"ok": True, "message": f"{student.name}: разморожен, возвращено дней: {returned_days}"}
    raise HTTPException(status_code=400, detail="Неизвестное действие")


@router.get("/webapp/staff-checkin", response_class=HTMLResponse)
async def staff_checkin_page(request: Request, club_id: int, init_data: str | None = Query(default=None), db: AsyncSession = Depends(get_session)):
    if not init_data:
        return _auth_gate_html("webapp/staff-checkin", club_id=club_id)
    club = await db.get(Club, club_id)
    tg_user = verify_telegram_data(init_data, club.bot_token) if club and club.bot_token else None
    if not tg_user:
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    user_id = int(tg_user["id"])
    staff = (await db.execute(select(ClubStaff).where(ClubStaff.club_id == club_id, ClubStaff.telegram_id == user_id, ClubStaff.is_active.is_(True)))).scalar_one_or_none()
    if int(club.owner_id or 0) != user_id and not (staff and staff_can(staff, "manual_checkin")):
        raise HTTPException(status_code=403, detail="Нет права отмечать посещения")
    return templates.TemplateResponse("staff_checkin.html", {"request": request, "club": club, "club_id": club_id, "user_id": user_id})


@router.get("/webapp/staff-checkin/search")
async def staff_checkin_search(club_id: int, q: str = Query(default=""), init_data: str = Query(default=""), db: AsyncSession = Depends(get_session)):
    club = await db.get(Club, club_id)
    tg_user = verify_telegram_data(init_data, club.bot_token) if club and club.bot_token else None
    if not tg_user:
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    user_id = int(tg_user["id"])
    staff = (await db.execute(select(ClubStaff).where(ClubStaff.club_id == club_id, ClubStaff.telegram_id == user_id, ClubStaff.is_active.is_(True)))).scalar_one_or_none()
    if int(club.owner_id or 0) != user_id and not (staff and staff_can(staff, "manual_checkin")):
        raise HTTPException(status_code=403, detail="Нет права отмечать посещения")
    q = (q or "").strip()
    stmt = select(Student).where(Student.club_id == club_id).order_by(Student.name).limit(30)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(Student.name.ilike(pattern) | Student.parent_phone.ilike(pattern))
    students = (await db.execute(stmt)).scalars().all()
    return [{"id": s.id, "name": s.name, "phone": s.parent_phone or "", "active": bool(s.expire_date and s.expire_date > datetime.now())} for s in students]


@router.post("/webapp/staff-checkin")
async def staff_checkin(payload: dict, db: AsyncSession = Depends(get_session)):
    club_id = int(payload.get("club_id", 0)); init_data = str(payload.get("init_data") or "")
    club = await db.get(Club, club_id)
    tg_user = verify_telegram_data(init_data, club.bot_token) if club and club.bot_token else None
    if not tg_user:
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    user_id = int(tg_user["id"])
    staff = (await db.execute(select(ClubStaff).where(ClubStaff.club_id == club_id, ClubStaff.telegram_id == user_id, ClubStaff.is_active.is_(True)))).scalar_one_or_none()
    if int(club.owner_id or 0) != user_id and not (staff and staff_can(staff, "manual_checkin")):
        raise HTTPException(status_code=403, detail="Нет права отмечать посещения")
    result = await process_athlete_gate_pass(int(payload["student_id"]), db, club.club_settings or {}, expected_club_id=club_id, open_turnstile=bool(payload.get("open_turnstile", False)))
    if not result.get("success"):
        raise HTTPException(status_code=409, detail=result.get("message", "Посещение не записано"))
    if not result.get("is_inside_session"):
        bot = Bot(club.bot_token)
        try:
            actor = "администратором" if int(club.owner_id or 0) == user_id else "сотрудником"
            for parent_id in await get_student_parent_ids(int(payload["student_id"]), db):
                await bot.send_message(parent_id, f"🔔 <b>Вход зафиксирован {actor}</b>\nАтлет: <b>{result['student_name']}</b>\n{result['message']}", parse_mode="HTML")
            if club.owner_id and int(club.owner_id) != user_id:
                await bot.send_message(int(club.owner_id), f"🟢 <b>Посещение отмечено сотрудником</b>\nАтлет: <b>{result['student_name']}</b>\nСотрудник ID: <code>{user_id}</code>\nРежим: {'с турникетом' if payload.get('open_turnstile') else 'без турникета'}", parse_mode="HTML")
        finally:
            await bot.session.close()
    active_until = None
    student = await db.get(Student, int(payload["student_id"]))
    if student and student.last_visit:
        active_until = (student.last_visit.replace(tzinfo=None) + timedelta(minutes=int((club.club_settings or {}).get("limits", {}).get("session_timeout_minutes", 150)))).isoformat()
    remaining_lessons = None
    if student:
        current_balance = int(getattr(student, "balance_lessons", 0) or 0)
        remaining_lessons = current_balance if current_balance == 999 else max(0, current_balance - (0 if result.get("is_inside_session") else 1))
    active_until_text = active_until.replace("T", " ")[:16] if active_until else ""
    balance_text = "безлимит" if remaining_lessons == 999 else (f"{remaining_lessons} занятий" if remaining_lessons is not None else "—")
    if result.get("already_marked"):
        operator_message = f"ℹ️ Посещение уже отмечено. Активная сессия до {active_until_text}. Осталось: {balance_text}."
    else:
        operator_message = f"✅ Посещение успешно отмечено. Сессия активна до {active_until_text}. После закрытия спишется 1 занятие. Осталось: {balance_text}."
    return {"success": True, "message": operator_message, "already_marked": result.get("already_marked", False), "active_until": active_until, "turnstile": result.get("turnstile_status"), "remaining_lessons": remaining_lessons}


@router.post("/webapp/staff-open-turnstile")
async def staff_open_turnstile(payload: dict, request: Request, db: AsyncSession = Depends(get_session)):
    club_id = int(payload.get("club_id", 0))
    init_data = str(payload.get("init_data") or "")
    club = await db.get(Club, club_id)
    if not club or not club.bot_token:
        raise HTTPException(status_code=404, detail="Клуб не найден")
    tg_user = verify_telegram_data(init_data, club.bot_token)
    if not tg_user:
        raise HTTPException(status_code=403, detail="Ошибка безопасности данных")
    user_id = int(tg_user.get("id", 0))
    is_staff_mode = await _is_staff_webapp_user(db, club, user_id)
    is_owner = int(club.owner_id or 0) == user_id
    is_staff = bool(
        is_owner
        or (
            await db.scalar(
                select(ClubStaff.id).where(
                    ClubStaff.club_id == club.id,
                    ClubStaff.telegram_id == user_id,
                    ClubStaff.is_active.is_(True),
                )
            )
        )
    )
    if not is_staff:
        raise HTTPException(status_code=403, detail="Доступ только для сотрудников")
    club_settings = club.club_settings or {}
    relay_config = dict(club_settings.get("turnstile", {}) or {})
    turnstile_status = "ℹ️ СКУД отключен"
    if relay_config.get("enabled", False):
        if not relay_config.get("base_url"):
            raise HTTPException(status_code=400, detail="Не настроен адрес турникета")
        if relay_config.get("base_url") and not str(relay_config.get("base_url")).startswith("http"):
            relay_config["base_url"] = f"http://{relay_config['base_url']}"
        opened = await trigger_dingtian_turnstile(relay_config)
        if not opened:
            raise HTTPException(status_code=409, detail="Не удалось открыть турникет")
        turnstile_status = "✅ Турникет открыт"
    audit_event(
        "staff_turnstile_opened",
        club_id=club.id,
        actor_user_id=user_id,
        actor_role="owner" if is_owner else "staff",
        action="open",
        object_type="turnstile",
        object_id="staff",
        location="webapp/staff-pass",
        method="manual",
        payload={"biometric_used": bool(payload.get("biometric_used", False))},
    )
    return {"success": True, "message": f"Проход для staff подтвержден.\n{turnstile_status}"}


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
    is_staff_mode = await _is_staff_webapp_user(db, club, user_id)
    user = await _ensure_webapp_user_linked(db, user_id, club_id)
    if not user and not is_staff_mode:
        return await webapp_auth_help_page(request=request, club_id=club_id, init_data=init_data, db=db)
    settings = (club.club_settings or {}) if club else {}
    students = (await db.execute(select(Student).outerjoin(StudentParent, StudentParent.student_id == Student.id).where(Student.club_id == club_id, or_(Student.parent_id == user_id, StudentParent.parent_id == user_id)).distinct().order_by(Student.name))).scalars().all()
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
            "profile_mode": "staff" if is_staff_mode else "client",
            "is_staff_mode": is_staff_mode,
            "logo_url": f"{_absolute_webapp_url(request, loading['logo_url'])}?v={loading.get('logo_rev', '')}" if loading.get("logo_url") else "",
            "students": students,
            "user_name": (user.full_name if user else None) or tg_user.get("first_name", ""),
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
        raise HTTPException(status_code=404, detail="Клуб не найден")
    tg_user = verify_telegram_data(init_data, club.bot_token)
    if not tg_user:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
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
        raise HTTPException(status_code=404, detail="Клуб не найден")
    tg_user = verify_telegram_data(payload.init_data, club.bot_token)
    if not tg_user:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    user_id = int(tg_user.get("id", 0))
    user = await _ensure_webapp_user_linked(db, user_id, payload.club_id)
    if not user:
        raise HTTPException(status_code=403, detail="Пользователь не привязан к клубу")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Введите имя атлета")
    birthday = None
    if payload.birthday:
        from services.input_normalization import parse_user_date
        try:
            birthday = parse_user_date(payload.birthday)
        except ValueError:
            raise HTTPException(status_code=400, detail="Некорректная дата рождения")
    existing = (await db.execute(select(Student).where(Student.club_id == club.id, Student.parent_id == user_id))).scalars().all()
    duplicate = next((student for student in existing if student.name.strip().casefold() == name.casefold() and student.birthday == birthday), None)
    if duplicate:
        raise HTTPException(status_code=409, detail="Такой атлет уже есть")
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
    if not student or student.club_id != club_id or int(tg_user.get("id", 0)) not in await get_student_parent_ids(student_id, db):
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
    timeout_minutes = int((club.club_settings or {}).get("limits", {}).get("session_timeout_minutes", 150))
    now = datetime.now()
    active_session = bool(student.last_visit and now - student.last_visit.replace(tzinfo=None) < timedelta(minutes=timeout_minutes))
    active_until = (student.last_visit.replace(tzinfo=None) + timedelta(minutes=timeout_minutes)) if active_session else None
    freeze_price_per_day = (club.club_settings or {}).get("limits", {}).get("freeze_price_per_day", 0)
    return templates.TemplateResponse("webapp_student.html", {"request": request, "club": club, "student": student, "club_id": club_id, "freeze_price_per_day": freeze_price_per_day, "visit_sessions": visit_sessions, "active_session": active_session, "active_until": active_until, "now": now})


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
    payment_info = get_payment_info_text(settings)
    return templates.TemplateResponse("webapp_buy_subscription.html", {"request": request, "club": club, "club_id": club_id, "students": students, "disciplines": disciplines, "sbp_enabled": sbp_enabled, "payment_info": payment_info})


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
    age_error = _tariff_age_error(student, selected_tariff, discipline_cfg.get("name", payload.sport_type))
    if age_error:
        raise HTTPException(status_code=400, detail=age_error)
    amount_kopecks = int(float(price) * 100)
    payment_method = _normalize_payment_method(payload.payment_method)
    pay_settings = (club.club_settings or {}).get("payments", {})
    shop_id = pay_settings.get("yookassa_shop_id")
    secret_key = pay_settings.get("yookassa_secret_key")
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
    order = PaymentOrder(id=order_id, user_id=user_id, student_id=student.id, club_id=club.id, amount_kopecks=amount_kopecks, lesson_count=count, days_to_add=days, discipline=payload.sport_type, status="NEW", type="FIRST", provider_payment_id=f"MANUAL:{order_id}")
    db.add(order)
    await db.commit()
    if payment_method == "requisites":
        payment_info = get_payment_info_text(club.club_settings or {})
        bot = Bot(club.bot_token)
        try:
            items_text = f"• {escape(discipline_cfg.get('name', payload.sport_type))}\n• {escape(str(selected_tariff.get('days', 30)))} дн."
            owner_text = build_payment_instruction_text(
                title="Новая заявка на абонемент по реквизитам",
                amount_kopecks=amount_kopecks,
                payment_info=payment_info,
                extra_lines=[
                    f"Клуб: <b>{escape(club.name)}</b>",
                    f"Атлет: <b>{escape(student.name)}</b>",
                    f"Направление: <b>{escape(discipline_cfg.get('name', payload.sport_type))}</b>",
                    f"Тариф: <b>{escape(str(selected_tariff.get('count', 0)))} / {escape(str(days))} дн.</b>",
                    "",
                    "После перевода админ подтвердит заявку вручную.",
                ],
            )
            if club.owner_id:
                await bot.send_message(club.owner_id, owner_text, parse_mode="HTML", reply_markup=_manual_review_keyboard(order_id))
        finally:
            await bot.session.close()
        audit_event("webapp_subscription_checkout_created", club_id=club.id, user_id=user_id, student_id=student.id, amount_kopecks=amount_kopecks, sport_type=payload.sport_type, tariff_idx=payload.tariff_idx, payment_method="requisites")
        return {"ok": True, "review_required": True, "message": f"Заявка по реквизитам отправлена администратору.\nРеквизиты: {payment_info}\nПосле подтверждения абонемент активируется."}
    if not shop_id or not secret_key:
        raise HTTPException(status_code=400, detail="ЮKassa не настроена")
    if payment_method == "sbp" and not bool(pay_settings.get("yookassa_sbp_enabled", True)):
        raise HTTPException(status_code=400, detail="СБП отключена в настройках клуба")
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
        user = User(user_id=user_id, club_id=None, full_name=tg_user.get("first_name") or "", is_accepted=False, is_biometric_enabled=False)
        db.add(user)
        await db.flush()
    for student in students:
        if student.parent_id is None:
            student.parent_id = user_id
        if not await db.get(StudentParent, {"student_id": student.id, "parent_id": user_id}):
            db.add(StudentParent(student_id=student.id, parent_id=user_id, is_primary=(student.parent_id == user_id)))
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
    payment_info = get_payment_info_text(settings)
    return templates.TemplateResponse("webapp_buy_freeze.html", {"request": request, "club": club, "student": student, "club_id": club_id, "price": price, "sbp_enabled": sbp_enabled, "payment_info": payment_info})


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
        provider_payment_id=f"MANUAL:{order_id}",
    )
    db.add(order)
    await db.commit()
    if payment_method == "requisites":
        payment_info = get_payment_info_text(club.club_settings or {})
        bot = Bot(club.bot_token)
        try:
            owner_text = build_payment_instruction_text(
                title="Новая заявка на заморозку по реквизитам",
                amount_kopecks=amount_kopecks,
                payment_info=payment_info,
                extra_lines=[
                    f"Клуб: <b>{escape(club.name)}</b>",
                    f"Атлет: <b>{escape(student.name)}</b>",
                    f"Дней: <b>{days}</b>",
                    "",
                    "После перевода админ подтвердит заявку вручную.",
                ],
            )
            if club.owner_id:
                await bot.send_message(club.owner_id, owner_text, parse_mode="HTML", reply_markup=_manual_review_keyboard(order_id))
        finally:
            await bot.session.close()
        audit_event("webapp_freeze_checkout_created", club_id=club.id, user_id=int(tg_user.get("id", 0)), student_id=student.id, days=days, amount_kopecks=amount_kopecks, payment_method="requisites")
        return {"ok": True, "review_required": True, "message": f"Заявка по реквизитам отправлена администратору.\nРеквизиты: {payment_info}\nПосле подтверждения заморозка активируется."}
    if not shop_id or not secret_key:
        raise HTTPException(status_code=400, detail="ЮKassa не настроена")
    if payment_method == "sbp" and not bool((club.club_settings or {}).get("payments", {}).get("yookassa_sbp_enabled", True)):
        raise HTTPException(status_code=400, detail="СБП отключена в настройках клуба")
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
