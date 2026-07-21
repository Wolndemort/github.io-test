import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl


def verify_telegram_data(init_data: str, bot_token: str) -> dict | None:
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated_hash, received_hash):
            return None
        auth_date = int(parsed.get("auth_date", "0"))
        max_age = int(os.getenv("TELEGRAM_INIT_DATA_MAX_AGE", "86400"))
        now = int(time.time())
        if auth_date <= 0 or auth_date > now + 300 or now - auth_date > max_age:
            return None
        user_data = json.loads(parsed.get("user", "{}"))
        return user_data
    except Exception:
        return None
