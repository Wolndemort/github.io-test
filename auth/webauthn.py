"""Feature-gated WebAuthn passkeys for the browser client.

Only credential metadata and public keys are persisted. The OS/browser retains
the biometric material and performs user verification locally.
"""
import base64
import json
import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fido2.server import Fido2Server
from fido2.webauthn import (AttestedCredentialData, PublicKeyCredentialRpEntity,
                            PublicKeyCredentialUserEntity)

from database.db import WebCredential, get_session
from .context import AuthContext
from .web_session import get_web_session, require_csrf, require_web_context

router = APIRouter(prefix="/webauthn", tags=["WebAuthn"])
TTL = 300

async def web_context(request: Request) -> AuthContext | None:
    return await get_web_session(request.app.state.redis_client, request)

def enabled() -> bool:
    return os.getenv("WEB_PASSKEYS_ENABLED", "0") == "1"

def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()

def unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

def server() -> Fido2Server:
    rp_id = os.getenv("WEBAUTHN_RP_ID", "localhost")
    return Fido2Server(PublicKeyCredentialRpEntity(id=rp_id, name=os.getenv("WEBAUTHN_RP_NAME", "SpeedyCRM")))

def guard() -> None:
    if not enabled():
        raise HTTPException(status_code=404, detail={"code": "feature_disabled"})

async def save(redis, key: str, value: dict) -> None:
    await redis.setex(key, TTL, json.dumps(value))

async def load(redis, key: str) -> dict:
    value = await redis.get(key)
    if not value:
        raise HTTPException(status_code=400, detail={"code": "challenge_expired"})
    await redis.delete(key)
    return json.loads(value.decode() if isinstance(value, bytes) else value)

class FinishPayload(BaseModel):
    response: dict
    device_label: str | None = None

@router.post("/register/options")
async def register_options(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    guard(); actor = require_web_context(context); await require_csrf(request.app.state.redis_client, request)
    user = PublicKeyCredentialUserEntity(id=str(actor.user_id).encode(), name=str(actor.user_id), display_name=str(actor.user_id))
    existing = (await session.scalars(select(WebCredential).where(WebCredential.user_id == actor.user_id, WebCredential.club_id == actor.club_id))).all()
    options, state = server().register_begin(user, [AttestedCredentialData(c.public_key) for c in existing] if existing else None)
    await save(request.app.state.redis_client, f"webauthn:register:{actor.user_id}:{actor.club_id}", state)
    return jsonable_encoder(options)

@router.post("/register/complete")
async def register_complete(payload: FinishPayload, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    guard(); actor = require_web_context(context); await require_csrf(request.app.state.redis_client, request)
    state = await load(request.app.state.redis_client, f"webauthn:register:{actor.user_id}:{actor.club_id}")
    try: data = server().register_complete(state, payload.response)
    except Exception as exc: raise HTTPException(status_code=400, detail={"code": "invalid_credential"}) from exc
    credential_data = data.credential_data
    if not credential_data: raise HTTPException(status_code=400, detail={"code": "missing_credential"})
    session.add(WebCredential(user_id=actor.user_id, club_id=actor.club_id, credential_id=bytes(credential_data.credential_id), public_key=bytes(credential_data), device_label=payload.device_label))
    await session.commit()
    return {"ok": True}

@router.post("/authenticate/options")
async def authenticate_options(request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    guard(); actor = require_web_context(context); await require_csrf(request.app.state.redis_client, request)
    creds = (await session.scalars(select(WebCredential).where(WebCredential.user_id == actor.user_id, WebCredential.club_id == actor.club_id))).all()
    if not creds: raise HTTPException(status_code=404, detail={"code": "no_credentials"})
    options, state = server().authenticate_begin([AttestedCredentialData(c.public_key) for c in creds])
    await save(request.app.state.redis_client, f"webauthn:auth:{actor.user_id}:{actor.club_id}", state)
    return jsonable_encoder(options)

@router.post("/authenticate/complete")
async def authenticate_complete(payload: FinishPayload, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    guard(); actor = require_web_context(context); await require_csrf(request.app.state.redis_client, request)
    state = await load(request.app.state.redis_client, f"webauthn:auth:{actor.user_id}:{actor.club_id}")
    creds = (await session.scalars(select(WebCredential).where(WebCredential.user_id == actor.user_id, WebCredential.club_id == actor.club_id))).all()
    try: result = server().authenticate_complete(state, [AttestedCredentialData(c.public_key) for c in creds], payload.response)
    except Exception as exc: raise HTTPException(status_code=401, detail={"code": "invalid_assertion"}) from exc
    credential_id = bytes(result.credential_id)
    row = next((c for c in creds if c.credential_id == credential_id), None)
    if not row: raise HTTPException(status_code=401, detail={"code": "unknown_credential"})
    row.sign_count = result.counter
    row.last_used_at = datetime.utcnow()
    await session.commit()
    return {"ok": True}

@router.get("/credentials")
async def credentials(context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    guard(); actor = require_web_context(context)
    rows = (await session.scalars(select(WebCredential).where(WebCredential.user_id == actor.user_id, WebCredential.club_id == actor.club_id))).all()
    return {"credentials": [{"id": c.id, "device_label": c.device_label, "created_at": c.created_at.isoformat()} for c in rows]}

@router.delete("/credentials/{credential_id}")
async def revoke(credential_id: int, request: Request, context: AuthContext | None = Depends(web_context), session: AsyncSession = Depends(get_session)):
    guard(); actor = require_web_context(context); await require_csrf(request.app.state.redis_client, request)
    row = await session.scalar(select(WebCredential).where(WebCredential.id == credential_id, WebCredential.user_id == actor.user_id, WebCredential.club_id == actor.club_id))
    if not row: raise HTTPException(status_code=404, detail={"code": "not_found"})
    await session.delete(row); await session.commit(); return {"ok": True}
