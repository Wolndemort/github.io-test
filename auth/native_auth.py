import asyncio
import hashlib
import os
import secrets
import smtplib
from email.message import EmailMessage

from redis.asyncio import Redis

OTP_TTL = 10 * 60
OTP_ATTEMPTS = 5


def normalize_email(value: str) -> str:
    return value.strip().lower()


def _key(email: str, club_id: int, purpose: str = "login") -> str:
    return f"web_native_otp:{purpose}:{club_id}:{normalize_email(email)}"


def _hash(code: str) -> str:
    secret = os.getenv("SECRET_KEY", "native-auth-test-secret")
    return hashlib.sha256(f"{secret}:{code}".encode()).hexdigest()


async def issue_otp(redis: Redis, email: str, club_id: int, purpose: str = "login") -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    await redis.hset(_key(email, club_id, purpose), mapping={"hash": _hash(code), "attempts": 0})
    await redis.expire(_key(email, club_id, purpose), OTP_TTL)
    return code


async def consume_otp(redis: Redis, email: str, club_id: int, code: str, purpose: str = "login") -> bool:
    key = _key(email, club_id, purpose)
    data = await redis.hgetall(key)
    if not data:
        return False
    attempts = int(data.get(b"attempts", data.get("attempts", 0)))
    if attempts >= OTP_ATTEMPTS:
        await redis.delete(key)
        return False
    await redis.hincrby(key, "attempts", 1)
    stored = data.get(b"hash", data.get("hash", ""))
    if isinstance(stored, bytes):
        stored = stored.decode()
    if secrets.compare_digest(str(stored), _hash(code)):
        await redis.delete(key)
        return True
    return False


def send_email_otp(recipient: str, code: str) -> None:
    host = os.getenv("SMTP_HOST")
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM", user or "")
    if not host or not sender:
        raise RuntimeError("SMTP is not configured")
    message = EmailMessage()
    message["Subject"] = "SpeedyCRM Web login code"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(f"Your SpeedyCRM login code is {code}. It expires in 10 minutes.")
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=10) as smtp:
        smtp.starttls()
        if user and password:
            smtp.login(user, password)
        smtp.send_message(message)


async def deliver_email_otp(recipient: str, code: str) -> None:
    await asyncio.to_thread(send_email_otp, recipient, code)
