from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_module.webapp_verify import verify_telegram_data
from database.db import Club, ClubStaff
from middlewares.db_saas_midleware import SUPER_ADMIN_IDS
from services.staff_permissions import staff_can


def telegram_init_gate(path: str, club_id: int, title: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><meta charset='utf-8'><script src='https://telegram.org/js/telegram-web-app.js'></script><script>const tg=window.Telegram.WebApp;tg.ready();if(!tg.initData)document.body.innerText='{title}';else location.replace('{path}?club_id={club_id}&init_data='+encodeURIComponent(tg.initData));</script>""",
        status_code=401,
    )


def get_club_id_from_host(request: Request) -> int:
    host = request.headers.get("host", "")
    subdomain = host.split(".")[0]

    if subdomain.isdigit():
        return int(subdomain)

    club_id_param = request.query_params.get("club_id")
    if club_id_param and club_id_param.isdigit():
        return int(club_id_param)

    return 0


def webapp_auth_gate(request: Request, club_id: int):
    return HTMLResponse(
        f"""<!doctype html><meta charset='utf-8'>
<script src='https://telegram.org/js/telegram-web-app.js'></script><script>
const tg=window.Telegram.WebApp; tg.ready();
if (!tg.initData) document.body.innerText='Откройте приложение из Telegram';
else location.replace(location.pathname+'?club_id={club_id}&init_data='+encodeURIComponent(tg.initData));
</script>""",
        status_code=401,
    )


async def verify_webapp_admin(club: Club, init_data: str | None):
    if not club or not getattr(club, "bot_token", None) or not init_data:
        raise HTTPException(status_code=403, detail="Требуется авторизация Telegram")
    tg_user = verify_telegram_data(init_data, club.bot_token)
    if not tg_user or (tg_user.get("id") != club.owner_id and tg_user.get("id") not in SUPER_ADMIN_IDS):
        raise HTTPException(status_code=403, detail="Доступ только для администратора клуба")
    return tg_user


async def verify_webapp_admin_or_manager(club: Club, init_data: str | None, session):
    if not club or not getattr(club, "bot_token", None) or not init_data:
        raise HTTPException(status_code=403, detail="Требуется авторизация Telegram")
    tg_user = verify_telegram_data(init_data, club.bot_token)
    if not tg_user:
        raise HTTPException(status_code=403, detail="Недействительные данные Telegram")
    user_id = int(tg_user.get("id", 0))
    if user_id == int(club.owner_id or 0) or user_id in {int(x) for x in SUPER_ADMIN_IDS}:
        return tg_user
    staff = (await session.execute(select(ClubStaff).where(
        ClubStaff.club_id == club.id, ClubStaff.telegram_id == user_id, ClubStaff.is_active.is_(True)
    ))).scalar_one_or_none()
    if not staff_can(staff, "athletes_manage"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    return tg_user


async def audit_actor_context(session: AsyncSession, club: Club, tg_user: dict | None, action_location: str | None = None) -> dict:
    user_id = int((tg_user or {}).get("id") or 0)
    actor_name = (tg_user or {}).get("first_name") or (tg_user or {}).get("username") or (tg_user or {}).get("last_name")
    owner_id = int(club.owner_id or 0)
    if user_id and user_id == owner_id:
        return {
            "actor_user_id": user_id,
            "actor_role": "owner",
            "actor_name": actor_name or "owner",
            "location": action_location,
        }
    if user_id in {int(x) for x in SUPER_ADMIN_IDS}:
        return {
            "actor_user_id": user_id,
            "actor_role": "super_admin",
            "actor_name": actor_name or "super_admin",
            "location": action_location,
        }
    staff = None
    if user_id:
        staff = (
            await session.execute(
                select(ClubStaff).where(
                    ClubStaff.club_id == club.id,
                    ClubStaff.telegram_id == user_id,
                    ClubStaff.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
    return {
        "actor_user_id": user_id or None,
        "actor_role": str(getattr(staff, "role", "")).strip().casefold() if staff else "client",
        "actor_name": getattr(staff, "full_name", None) or actor_name,
        "location": action_location,
    }
