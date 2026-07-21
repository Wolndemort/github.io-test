import json
from urllib.parse import parse_qsl

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_module.router_base import router
from admin_module.schemas import BiometricCheckIn, BiometricEnable
from admin_module.webapp_verify import verify_telegram_data
from admin_module.security import get_api_key
from database.db import Club, Student, User, get_session
from handlers.skud import trigger_dingtian_turnstile
from services.gate_control import process_athlete_gate_pass


@router.post("/open-turnstile")
async def open_turnstile(payload: dict, db: AsyncSession = Depends(get_session), _: str = Depends(get_api_key)):
    student_id = payload.get("student_id")
    student_club = await db.execute(select(Student.club_id).where(Student.id == student_id))
    club_id = student_club.scalar()
    club_res = await db.execute(select(Club.club_settings).where(Club.id == club_id))
    club_settings = club_res.scalar() or {}
    res = await process_athlete_gate_pass(student_id, db, club_settings, expected_club_id=club_id)
    if not res["success"]:
        return {"success": False, "message": res["message"]}
    return {"success": True, "message": f"{res['message']} | {res['turnstile_status']}"}


@router.post("/webapp/open-turnstile")
async def open_webapp_turnstile(payload: BiometricCheckIn, request: Request, db: AsyncSession = Depends(get_session)):
    student_res = await db.execute(select(Student).where(Student.id == payload.student_id))
    student = student_res.scalar_one_or_none()
    if not student:
        raise HTTPException(status_code=404, detail="Студент не найден")
    club_res = await db.execute(select(Club).where(Club.id == student.club_id))
    club = club_res.scalar_one_or_none()
    if not club or not club.bot_token:
        raise HTTPException(status_code=400, detail="Конфигурация клуба не найдена")
    tg_user = verify_telegram_data(payload.init_data, club.bot_token)
    if not tg_user or "id" not in tg_user:
        raise HTTPException(status_code=403, detail="Ошибка безопасности: Неверные данные WebApp")
    if student.parent_id != tg_user["id"]:
        raise HTTPException(status_code=403, detail="Доступ запрещен: Вы не родитель этого атлета")
    club_settings = club.club_settings or {}
    res = await process_athlete_gate_pass(payload.student_id, db, club_settings, expected_club_id=club.id)
    if not res["success"]:
        return {"success": False, "message": res["message"]}
    if not res["is_inside_session"] and student.parent_id:
        try:
            bots_dict = getattr(request.app.state, "bots_dict", {})
            bot = bots_dict.get(club.bot_token)
            if bot:
                await bot.send_message(chat_id=int(student.parent_id), text=f"🔔 <b>{club.name}</b>: {res['student_name']} вошел в зал (через WebApp кнопку).", parse_mode="HTML")
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
    user_res = await db.execute(select(User).where(User.user_id == telegram_user_id))
    parent_user = user_res.scalar_one_or_none()
    if not parent_user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    club_res = await db.execute(select(Club).where(Club.id == parent_user.club_id))
    club = club_res.scalar_one_or_none()
    if not club or not verify_telegram_data(payload.init_data, club.bot_token):
        raise HTTPException(status_code=430, detail="Ошибка безопасности данных")
    parent_user.is_biometric_enabled = True
    await db.commit()
    return {"success": True, "message": "Биометрия успешно активирована в профиле!"}
