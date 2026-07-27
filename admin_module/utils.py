from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from database.db import Club, ClubStaff
from middlewares.db_saas_midleware import SUPER_ADMIN_IDS
from .webapp_verify import verify_telegram_data
from services.staff_permissions import staff_can


async def verify_webapp_staff(club: Club, init_data: str | None, session, permission: str):
    """Owner/super keep full access; staff receives only an explicit permission."""
    if not club or not getattr(club, "bot_token", None) or not init_data:
        raise HTTPException(status_code=403, detail="Требуется авторизация Telegram")
    tg_user = verify_telegram_data(init_data, club.bot_token)
    if not tg_user:
        raise HTTPException(status_code=403, detail="Недействительные данные Telegram")
    user_id = int(tg_user.get("id"))
    if user_id == int(club.owner_id or 0) or user_id in SUPER_ADMIN_IDS:
        return tg_user
    staff_obj = (await session.execute(
        select(ClubStaff).where(
            ClubStaff.club_id == club.id,
            ClubStaff.telegram_id == user_id,
            ClubStaff.is_active.is_(True),
        )
    )).scalar_one_or_none()
    if not staff_can(staff_obj, permission):
        raise HTTPException(status_code=403, detail="Доступ запрещён для этой роли")
    return tg_user


async def is_active_staff_member(session, club_id: int, telegram_id: int) -> bool:
    staff = (
        await session.execute(
            select(ClubStaff).where(
                ClubStaff.club_id == club_id,
                ClubStaff.telegram_id == telegram_id,
                ClubStaff.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    return bool(staff)


async def is_staff_or_owner(session, club: Club, telegram_id: int) -> bool:
    if int(getattr(club, "owner_id", 0) or 0) == int(telegram_id):
        return True
    return await is_active_staff_member(session, club.id, telegram_id)


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
    try:
        telegram_user_id = int(tg_user.get("id")) if tg_user else None
        owner_id = int(club.owner_id) if club.owner_id is not None else None
    except (TypeError, ValueError):
        telegram_user_id = owner_id = None
    if telegram_user_id is None or (telegram_user_id != owner_id and telegram_user_id not in {int(x) for x in SUPER_ADMIN_IDS}):
        raise HTTPException(status_code=403, detail="Доступ только для администратора клуба")
    return tg_user
