import json
import secrets
from datetime import datetime, timezone

from fastapi import HTTPException, Request
from redis.asyncio import Redis

from .context import AuthContext

SESSION_COOKIE = "speedycrm_web_session"
CSRF_COOKIE = "speedycrm_csrf_token"
CSRF_HEADER = "x-csrf-token"
SESSION_TTL_SECONDS = 60 * 60 * 12
SESSION_KEY_PREFIX = "web_session:"


def _session_key(session_id: str) -> str:
    return f"{SESSION_KEY_PREFIX}{session_id}"


async def create_web_session(redis: Redis, context: AuthContext) -> str:
    session_id = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    payload = {
        "user_id": context.user_id,
        "club_id": context.club_id,
        "actor_type": context.actor_type,
        "role": context.role,
        "permissions": sorted(context.permissions),
        "auth_source": context.auth_source,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "csrf_token": csrf_token,
    }
    await redis.setex(_session_key(session_id), SESSION_TTL_SECONDS, json.dumps(payload))
    return session_id


async def get_web_session(redis: Redis, request: Request) -> AuthContext | None:
    session_id = request.cookies.get(SESSION_COOKIE)
    if not session_id:
        return None
    raw = await redis.get(_session_key(session_id))
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)
    await redis.expire(_session_key(session_id), SESSION_TTL_SECONDS)
    return AuthContext(
        user_id=int(data["user_id"]),
        club_id=int(data["club_id"]),
        actor_type=str(data["actor_type"]),
        role=str(data["role"]),
        permissions=frozenset(data.get("permissions", [])),
        auth_source=str(data["auth_source"]),
    )


async def revoke_web_session(redis: Redis, request: Request) -> None:
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id:
        await redis.delete(_session_key(session_id))


def set_session_cookie(response, session_id: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


async def validate_csrf(redis: Redis, request: Request) -> bool:
    session_id = request.cookies.get(SESSION_COOKIE)
    csrf_token = request.headers.get(CSRF_HEADER)
    if not session_id or not csrf_token:
        return False
    raw = await redis.get(_session_key(session_id))
    if not raw:
        return False
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    stored = json.loads(raw).get("csrf_token")
    return bool(stored) and secrets.compare_digest(str(stored), csrf_token)


def set_csrf_cookie(response, csrf_token: str) -> None:
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=SESSION_TTL_SECONDS,
        httponly=False,
        secure=True,
        samesite="lax",
        path="/",
    )


def require_web_context(context: AuthContext | None) -> AuthContext:
    if context is None:
        raise HTTPException(status_code=401, detail={"code": "not_authenticated", "message": "Требуется авторизация"})
    return context


async def require_csrf(redis: Redis, request: Request) -> None:
    """Guard for every future Web POST/PATCH/DELETE mutation."""
    if not await validate_csrf(redis, request):
        raise HTTPException(status_code=403, detail={"code": "csrf_failed", "message": "Недействительный CSRF-токен"})
