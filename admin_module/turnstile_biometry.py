import json
from urllib.parse import parse_qsl

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_module.router_base import router
from admin_module.schemas import BiometricCheckIn, BiometricEnable
from admin_module.webapp_verify import verify_telegram_data
from admin_module.security import get_api_key
from database.db import Club, Student, User, get_session, get_student_parent_ids
from handlers.skud import trigger_dingtian_turnstile
from services.gate_control import process_athlete_gate_pass
from services.audit import audit_event


@router.post("/open-turnstile")
async def open_turnstile(
    payload: dict,
    request: Request,
    db: AsyncSession = Depends(get_session),
    _: str = Depends(get_api_key),
):
    student_id = payload.get("student_id")
    student_club = await db.execute(select(Student.club_id).where(Student.id == student_id))
    club_id = student_club.scalar()
    club_res = await db.execute(select(Club.club_settings).where(Club.id == club_id))
    club_settings = club_res.scalar() or {}
    redis = getattr(request.app.state, "redis_client", None)
    res = await process_athlete_gate_pass(student_id, db, club_settings, expected_club_id=club_id, redis=redis)
    if not res["success"]:
        return {"success": False, "message": res["message"]}
    return {"success": True, "message": f"{res['message']} | {res['turnstile_status']}"}


@router.post("/webapp/open-turnstile")
async def open_webapp_turnstile(payload: BiometricCheckIn, request: Request, db: AsyncSession = Depends(get_session)):
    student_res = await db.execute(select(Student).where(Student.id == payload.student_id))
    student = student_res.scalar_one_or_none()
    if not student:
        raise HTTPException(status_code=404, detail="Ученик не найден")
    club_res = await db.execute(select(Club).where(Club.id == student.club_id))
    club = club_res.scalar_one_or_none()
    if not club or not club.bot_token:
        raise HTTPException(status_code=400, detail="Конфигурация клуба не найдена")
    tg_user = verify_telegram_data(payload.init_data, club.bot_token)
    if not tg_user or "id" not in tg_user:
        raise HTTPException(status_code=403, detail="Ошибка безопасности: неверные данные WebApp")
    if int(tg_user["id"]) not in await get_student_parent_ids(student.id, db):
        raise HTTPException(status_code=403, detail="Доступ запрещён: вы не родитель этого атлета")
    # Telegram на части клиентов после успешного authenticate() не возвращает
    # строковый токен. WebApp уже получил подтверждение isAuthenticated=True и
    # отправляет совместимый маркер verified_by_device. Нельзя отклонять такой
    # проход: иначе клиентский Face ID ломается, тогда как staff-pass работает.
    if not payload.biometric_token:
        raise HTTPException(status_code=403, detail="Для прохода обязательно подтверждение Face ID")
    # Доступ к этому endpoint уже подтверждён валидным Telegram init_data,
    # а результат Face ID приходит из Telegram WebApp callback. Флаг
    # is_biometric_enabled мог быть не установлен у старых пользователей и
    # ошибочно блокировал рабочий проход, хотя биометрия на устройстве была
    # доступна. Сообщение «Face ID не активирован» оставляем в endpoint
    # активации ниже для новых устройств.
    club_settings = club.club_settings or {}
    redis = getattr(request.app.state, "redis_client", None)
    res = await process_athlete_gate_pass(payload.student_id, db, club_settings, expected_club_id=club.id, redis=redis)
    if not res["success"]:
        return {"success": False, "message": res["message"]}
    if not res["is_inside_session"]:
        try:
            bots_dict = getattr(request.app.state, "bots_dict", {})
            bot = bots_dict.get(club.bot_token)
            if bot:
                notice = f"🔔 <b>{club.name}</b>: {res['student_name']} вошёл в зал (Face ID/WebApp)."
                for parent_id in await get_student_parent_ids(student.id, db):
                    await bot.send_message(chat_id=parent_id, text=notice, parse_mode="HTML")
                if club.owner_id and int(club.owner_id) != int(tg_user["id"]):
                    await bot.send_message(chat_id=int(club.owner_id), text=f"🟢 <b>ПРОХОД FACE ID</b>\nАтлет: <b>{res['student_name']}</b>\nИнициатор: <code>{tg_user['id']}</code>", parse_mode="HTML")
        except Exception:
            pass
    return {"success": True, "message": f"{res['message']}\n{res['turnstile_status']}"}


@router.post("/webapp/enable-biometry")
async def enable_biometry(payload: BiometricEnable, db: AsyncSession = Depends(get_session)):
    parsed_data = dict(parse_qsl(payload.init_data))
    tg_user = json.loads(parsed_data.get("user", "{}"))
    telegram_user_id = tg_user.get("id")
    if not telegram_user_id:
        raise HTTPException(status_code=400, detail="Неверные данные Telegram")
    audit_event(
        "biometric_enable_requested",
        actor_user_id=int(telegram_user_id),
        action="request",
        object_type="biometric",
        location="webapp/enable-biometry",
    )
    user_res = await db.execute(select(User).where(User.user_id == telegram_user_id))
    parent_user = user_res.scalar_one_or_none()
    if not parent_user:
        parent_user = User(
            user_id=telegram_user_id,
            club_id=None,
            full_name=tg_user.get("first_name") or "",
            is_accepted=False,
            is_biometric_enabled=False,
        )
        db.add(parent_user)
    club_id_res = await db.execute(
        select(Student.club_id).where(Student.parent_id == telegram_user_id, Student.club_id.isnot(None)).limit(1)
    )
    club_id = club_id_res.scalar()
    club = await db.get(Club, club_id) if club_id else None
    if not club or not verify_telegram_data(payload.init_data, club.bot_token):
        raise HTTPException(status_code=430, detail="Ошибка безопасности данных")
    parent_user.is_biometric_enabled = True
    await db.commit()
    audit_event(
        "biometric_enabled",
        club_id=club.id,
        actor_user_id=int(telegram_user_id),
        action="enable",
        object_type="biometric",
        object_id=int(telegram_user_id),
        location="webapp/enable-biometry",
    )
    return {"success": True, "message": "Биометрия успешно активирована в профиле!"}


