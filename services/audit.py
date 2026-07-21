from __future__ import annotations

from loguru import logger


def audit_event(event: str, **fields):
    payload = {"event": event, **fields}
    logger.bind(audit=True).info("AUDIT {event} | {payload}", event=event, payload=payload)
