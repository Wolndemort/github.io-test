from __future__ import annotations

import asyncio
from datetime import date, datetime
from decimal import Decimal

from loguru import logger

from database.db import AuditEntry, AsyncSessionLocal


def _jsonable(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return str(value)


def _split_audit_fields(fields: dict) -> dict:
    payload = {k: _jsonable(v) for k, v in fields.items()}
    return payload


async def _persist_audit(event: str, payload: dict):
    try:
        async with AsyncSessionLocal() as session:
            entry = AuditEntry(
                club_id=payload.get("club_id"),
                event=event,
                actor_user_id=payload.get("actor_user_id"),
                actor_role=payload.get("actor_role"),
                action=payload.get("action"),
                object_type=payload.get("object_type"),
                object_id=str(payload.get("object_id")) if payload.get("object_id") is not None else None,
                location=payload.get("location"),
                amount_kopecks=payload.get("amount_kopecks"),
                method=payload.get("method"),
                payload=payload,
            )
            session.add(entry)
            await session.commit()
    except Exception as exc:
        logger.warning("Не удалось сохранить audit entry %s: %s", event, exc)


def audit_event(event: str, **fields):
    payload = {"event": event, **_split_audit_fields(fields)}
    logger.bind(audit=True).info("AUDIT {event} | {payload}", event=event, payload=payload)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_persist_audit(event, payload))
