from __future__ import annotations

import asyncio
import uuid

from aiogram.exceptions import TelegramRetryAfter


def _redis():
    # Imported lazily to avoid importing main while routers are being built.
    from main import redis_client
    return redis_client


async def claim_once(key: str, ttl: int = 900) -> str | None:
    """Atomically claim an operation. Redis failure is fail-closed."""
    token = uuid.uuid4().hex
    try:
        if await _redis().set(key, token, ex=ttl, nx=True):
            return token
    except Exception:
        return None
    return None


async def release_claim(key: str, token: str | None) -> None:
    if not token:
        return
    try:
        await _redis().eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
            1,
            key,
            token,
        )
    except Exception:
        pass


async def mark_once(key: str, ttl: int = 90 * 86400) -> bool:
    """Mark a delivery exactly once; Redis failure blocks sending."""
    try:
        return bool(await _redis().set(key, "1", ex=ttl, nx=True))
    except Exception:
        return False


async def forget(key: str) -> None:
    try:
        await _redis().delete(key)
    except Exception:
        pass


async def telegram_pace(channel: str, interval_ms: int = 60) -> None:
    """Coordinate Telegram sends across all API workers for one bot."""
    key = f"tg:pace:{channel}"
    while True:
        token = uuid.uuid4().hex
        try:
            if await _redis().set(key, token, px=interval_ms, nx=True):
                return
        except Exception as exc:
            raise RuntimeError("Telegram pacing is unavailable") from exc
        await asyncio.sleep(0.01)


async def telegram_call(call, attempts: int = 3):
    """Retry Telegram flood-wait responses without duplicating normal errors."""
    for attempt in range(attempts):
        try:
            return await call()
        except TelegramRetryAfter as exc:
            if attempt + 1 >= attempts:
                raise
            await asyncio.sleep(float(exc.retry_after) + 0.25)
