from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from database.db import Club
from middlewares.db_saas_midleware import SUPER_ADMIN_IDS
from .webapp_verify import verify_telegram_data


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
    if not tg_user or (tg_user.get("id") != club.owner_id and tg_user.get("id") not in SUPER_ADMIN_IDS):
        raise HTTPException(status_code=403, detail="Доступ только для администратора клуба")
