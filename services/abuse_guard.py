from __future__ import annotations

from loguru import logger
from redis.asyncio import Redis

from services.audit import audit_event


async def rate_limit(redis: Redis, key: str, limit: int, window_sec: int) -> bool:
    """
    Простой Redis rate limit: True если запрос разрешён, False если лимит превышен.
    """
    try:
        current = await redis.incr(key)
        if current == 1:
            await redis.expire(key, window_sec)
        return current <= limit
    except Exception as exc:
        logger.warning(f"Rate limit check failed for {key}: {exc}")
        # Платёжные и binding-операции должны быть fail-closed: при падении
        # Redis лучше временно отказать, чем пропустить спам/дубли.
        return False


async def audit_block(event: str, reason: str, **fields):
    audit_event(event, blocked=True, reason=reason, **fields)
