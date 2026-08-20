import os
import logging

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
    require_csrf,
    require_web_context,
    revoke_web_session,
    set_csrf_cookie,
    set_session_cookie,
    validate_csrf,
)
from .native_auth import consume_otp, deliver_email_otp, issue_otp, normalize_email

router = APIRouter(prefix="/auth", tags=["Authentication"])
logger = logging.getLogger(__name__)


async def web_context(request: Request) -> AuthContext | None:
    return await get_web_session(request.app.state.redis_client, request)


class TelegramExchangePayload(BaseModel):
    init_data: str
    club_id: int


class NativeOtpRequest(BaseModel):
    email: str
    club_id: int


class NativeOtpVerify(NativeOtpRequest):
    code: str


def native_auth_enabled() -> bool:
    return os.getenv("WEB_NATIVE_AUTH_ENABLED", "0") == "1"


def native_email_binding_enabled() -> bool:
    return os.getenv("WEB_NATIVE_EMAIL_BINDING_ENABLED", "0") == "1"


@router.post("/native/email/request")
async def native_email_bind_request(payload: NativeOtpRequest, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    if not native_email_binding_enabled():
        raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    actor = require_web_context(context)
    await require_csrf(request.app.state.redis_client, request)
    email = normalize_email(payload.email)
    if payload.club_id != actor.club_id or not email or "@" not in email:
        raise HTTPException(status_code=400, detail={"code": "invalid_email"})
    code = await issue_otp(request.app.state.redis_client, email, actor.club_id, purpose="bind")
    await deliver_email_otp(email, code)
    return {"ok": True, "message": "Verification code sent."}


@router.post("/native/email/verify")
async def native_email_bind_verify(payload: NativeOtpVerify, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    if not native_email_binding_enabled():
        raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    actor = require_web_context(context)
    await require_csrf(request.app.state.redis_client, request)
    email = normalize_email(payload.email)
    if payload.club_id != actor.club_id or not await consume_otp(request.app.state.redis_client, email, actor.club_id, payload.code, purpose="bind"):
        raise HTTPException(status_code=401, detail={"code": "invalid_code"})
    user = await session.scalar(select(User).where(User.user_id == actor.user_id))
    if not user:
        user = User(user_id=actor.user_id, club_id=actor.club_id)
        session.add(user)
    user.email = email
    await session.commit()
    return {"ok": True, "email": email}


@router.post("/native/request")
async def native_request(payload: NativeOtpRequest, request: Request, session: AsyncSession = Depends(get_session)):
    if not native_auth_enabled():
        raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    email = normalize_email(payload.email)
    club = await session.scalar(select(Club).where(Club.id == payload.club_id))
    user = await session.scalar(select(User).where(User.club_id == payload.club_id, User.email == email)) if club else None
    if user:
        code = await issue_otp(request.app.state.redis_client, email, payload.club_id)
        try:
            await deliver_email_otp(email, code)
        except Exception:
            logger.exception("native email delivery failed")
            raise HTTPException(status_code=503, detail={"code": "email_delivery_unavailable"})
    return {"ok": True, "message": "If the account exists, a code was sent."}


@router.post("/native/verify")
async def native_verify(payload: NativeOtpVerify, request: Request, session: AsyncSession = Depends(get_session)):
    if not native_auth_enabled():
        raise HTTPException(status_code=404, detail={"code": "feature_disabled"})
    email = normalize_email(payload.email)
    user = await session.scalar(select(User).where(User.club_id == payload.club_id, User.email == email))
    if not user or not await consume_otp(request.app.state.redis_client, email, payload.club_id, payload.code):
        raise HTTPException(status_code=401, detail={"code": "invalid_code"})
    club = await session.scalar(select(Club).where(Club.id == payload.club_id))
    staff = await session.scalar(select(ClubStaff).where(ClubStaff.club_id == payload.club_id, ClubStaff.telegram_id == user.user_id, ClubStaff.is_active.is_(True)))
    if user.user_id == int(club.owner_id or 0):
        actor_type, role, permissions = "owner", "owner", frozenset()
    elif staff:
        actor_type, role, permissions = "staff", staff.role, frozenset(permissions_for_staff(staff))
    else:
        actor_type, role, permissions = "client", "client", frozenset()
    session_id = await create_web_session(request.app.state.redis_client, AuthContext(user.user_id, payload.club_id, actor_type, role, permissions, "email"))
    response = JSONResponse({"ok": True, "redirect": "/staff" if actor_type != "client" else "/client/cabinet"})
    set_session_cookie(response, session_id)
    raw_session = await request.app.state.redis_client.get(f"web_session:{session_id}")
    if isinstance(raw_session, bytes): raw_session = raw_session.decode("utf-8")
    import json
    set_csrf_cookie(response, json.loads(raw_session)["csrf_token"])
    return response


@router.get("/web-entry", response_class=HTMLResponse)
async def auth_web_entry(club_id: int):
    """Telegram WebApp entry point; initData is exchanged server-side."""
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SpeedyCRM Web</title><script src="https://telegram.org/js/telegram-web-app.js"></script></head><body><main><p id="status">Connecting…</p></main><script>
const status=document.getElementById("status");
const tg=window.Telegram&&window.Telegram.WebApp;
if(!tg||!tg.initData){{status.textContent="Open this page from the staging Telegram bot.";}}
else{{tg.ready();fetch("/auth/telegram/exchange",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{init_data:tg.initData,club_id:{club_id}}})}}).then(async r=>{{if(!r.ok)throw new Error(await r.text());return r.json();}}).then(d=>location.replace(d.redirect||"/staff")).catch(e=>{{status.textContent="Authentication failed.";console.error(e);}});}}
</script></body></html>''')


@router.get("/email-profile", response_class=HTMLResponse)
async def email_profile_page(context: AuthContext | None = Depends(web_context)):
    require_web_context(context)
    return HTMLResponse('''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Email login · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><main class="web-shell"><div class="web-container"><div id="navigation"></div><section class="web-hero"><span class="web-kicker">Account security</span><h1>One account,<br>two doors.</h1><p>Bind an email to use Web without Telegram.</p></section><section class="web-card" id="email-binding"></section></div></main><script>document.querySelector("#navigation").innerHTML=SpeedyCRMWeb.navigation("Account / Email");SpeedyCRMWeb.mountEmailBinding("email-binding");</script></body></html>''')


@router.get("/native-login", response_class=HTMLResponse)
async def native_login_page(club_id: int):
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Email login · SpeedyCRM</title><link rel="stylesheet" href="/static/web/design.css"><script src="/static/web/components.js"></script></head><body><main class="web-shell"><div class="web-container"><section class="web-hero"><span class="web-kicker">SpeedyCRM / Web</span><h1>Sign in,<br>securely.</h1><p>Use your verified email. Telegram is not required.</p></section><section class="web-card"><form id="native-login"><label>Email <input type="email" name="email" required autocomplete="email"></label><button type="submit">Send code</button></form><form id="native-verify" hidden><label>Code <input name="code" inputmode="numeric" pattern="[0-9]{6}" required autocomplete="one-time-code"></label><button type="submit">Sign in</button></form><div id="native-result" role="status"></div></section></div></main><script>const clubId={club_id};const login=document.querySelector("#native-login"),verify=document.querySelector("#native-verify"),result=document.querySelector("#native-result");let email="";const json=(url,options)=>fetch(url,options).then(async r=>{{if(!r.ok)throw new Error(await r.text());return r.json()}});login.addEventListener("submit",async e=>{{e.preventDefault();email=new FormData(login).get("email");try{{await json("/auth/native/request",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{email,club_id:clubId}})}});login.hidden=true;verify.hidden=false;result.textContent="Code sent."}}catch(_ ){{result.textContent="Unable to send code."}}}});verify.addEventListener("submit",async e=>{{e.preventDefault();try{{const code=new FormData(verify).get("code");const data=await json("/auth/native/verify",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{email,club_id:clubId,code}})}});location.replace(data.redirect||"/staff")}}catch(_ ){{result.textContent="Invalid or expired code."}}}});</script></body></html>''')


@router.get("/login")
async def auth_login():
    return {"ok": True, "method": "telegram_exchange", "exchange_endpoint": "/auth/telegram/exchange", "message": "Откройте Web через Telegram и выполните одноразовый exchange"}


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
async def auth_me(context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    actor = require_web_context(context)
    user = await session.scalar(select(User).where(User.user_id == actor.user_id, User.club_id == actor.club_id))
    return {
        "user_id": actor.user_id,
        "club_id": actor.club_id,
        "actor_type": actor.actor_type,
        "role": actor.role,
        "permissions": sorted(actor.permissions),
        "auth_source": actor.auth_source,
        "email": user.email if user else None,
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
