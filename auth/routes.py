from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import Club, ClubStaff, Student, User, get_session
from middlewares.db_saas_midleware import SUPER_ADMIN_IDS
from admin_module.webapp_verify import verify_telegram_data
from services.staff_permissions import permissions_for_staff
from services.audit import audit_event

from .context import AuthContext
from .web_session import (
    create_web_session,
    get_web_session,
    require_web_context,
    revoke_web_session,
    set_csrf_cookie,
    set_session_cookie,
    validate_csrf,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.get("/web-entry", response_class=HTMLResponse)
async def auth_web_entry(club_id: int):
    """Telegram WebApp entry point; initData is exchanged server-side."""
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SpeedyCRM Web</title><script src="https://telegram.org/js/telegram-web-app.js"></script></head><body><main><p id="status">Connecting…</p></main><script>
const status=document.getElementById("status");
const tg=window.Telegram&&window.Telegram.WebApp;
if(!tg||!tg.initData){{status.textContent="Open this page from the staging Telegram bot.";}}
else{{tg.ready();fetch("/auth/telegram/exchange",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{init_data:tg.initData,club_id:{club_id}}})}}).then(async r=>{{if(!r.ok)throw new Error(await r.text());return r.json();}}).then(d=>location.replace(d.redirect||"/staff")).catch(e=>{{status.textContent="Authentication failed.";console.error(e);}});}}
</script></body></html>''')


class TelegramExchangePayload(BaseModel):
    init_data: str
    club_id: int


@router.get("/login")
async def auth_login():
    return {"ok": True, "method": "telegram_exchange", "exchange_endpoint": "/auth/telegram/exchange", "message": "Откройте Web через Telegram и выполните одноразовый exchange"}


async def web_context(request: Request) -> AuthContext | None:
    return await get_web_session(request.app.state.redis_client, request)


@router.post("/telegram/exchange")
async def telegram_exchange(
    payload: TelegramExchangePayload,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    club = await session.scalar(select(Club).where(Club.id == payload.club_id))
    if not club or not club.bot_token:
        raise HTTPException(status_code=401, detail={"code": "invalid_club", "message": "Клуб не найден"})
    tg_user = verify_telegram_data(payload.init_data, club.bot_token)
    if not tg_user or not tg_user.get("id"):
        raise HTTPException(status_code=401, detail={"code": "invalid_telegram_auth", "message": "Недействительные данные Telegram"})

    user_id = int(tg_user["id"])
    if user_id == int(club.owner_id or 0) or user_id in {int(x) for x in SUPER_ADMIN_IDS}:
        role, permissions = "owner", frozenset()
        actor_type = "owner"
    else:
        staff = await session.scalar(select(ClubStaff).where(
            ClubStaff.club_id == club.id,
            ClubStaff.telegram_id == user_id,
            ClubStaff.is_active.is_(True),
        ))
        client_user = await session.scalar(select(User).where(User.user_id == user_id, User.club_id == club.id))
        client_student = await session.scalar(select(Student).where(Student.club_id == club.id, Student.parent_id == user_id))
        if not staff and not client_user and not client_student:
            raise HTTPException(status_code=403, detail={"code": "staff_access_required", "message": "Нет доступа к staff Web"})
        if staff:
            role, permissions = staff.role, frozenset(permissions_for_staff(staff))
            actor_type = "staff"
        else:
            role, permissions = "client", frozenset()
            actor_type = "client"

    context = AuthContext(user_id, club.id, actor_type, role, permissions, "telegram")
    audit_event("web_auth_exchange", club_id=club.id, actor_user_id=user_id, actor_role=role, action="login", location="web/auth/telegram/exchange", auth_source="telegram")
    session_id = await create_web_session(request.app.state.redis_client, context)
    response = JSONResponse({"ok": True, "redirect": "/staff/forecast"})
    set_session_cookie(response, session_id)
    raw_session = await request.app.state.redis_client.get(f"web_session:{session_id}")
    if isinstance(raw_session, bytes):
        raw_session = raw_session.decode("utf-8")
    import json
    set_csrf_cookie(response, json.loads(raw_session)["csrf_token"])
    return response


@router.get("/me")
async def auth_me(context: AuthContext | None = Depends(web_context)):
    actor = require_web_context(context)
    return {
        "user_id": actor.user_id,
        "club_id": actor.club_id,
        "actor_type": actor.actor_type,
        "role": actor.role,
        "permissions": sorted(actor.permissions),
        "auth_source": actor.auth_source,
    }


@router.post("/logout")
async def auth_logout(request: Request):
    if not await validate_csrf(request.app.state.redis_client, request):
        raise HTTPException(status_code=403, detail={"code": "csrf_failed", "message": "Недействительный CSRF-токен"})
    await revoke_web_session(request.app.state.redis_client, request)
    audit_event("web_auth_logout", action="logout", location="web/auth/logout", auth_source="web")
    response = JSONResponse({"ok": True})
    response.delete_cookie("speedycrm_web_session", path="/")
    response.delete_cookie("speedycrm_csrf_token", path="/")
    return response
