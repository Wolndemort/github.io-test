from __future__ import annotations

from math import isfinite


LIMIT_DEFAULTS = {
    "session_timeout_minutes": 150,
    "freeze_days_step": 7,
    "freeze_price_per_day": 0.0,
    "subscription_days": 30,
}


def normalize_limit_updates(values: dict) -> dict:
    """Validate the canonical club limits shared by bot and WebApp."""
    if not isinstance(values, dict):
        raise ValueError("limits must be an object")
    unknown = set(values) - set(LIMIT_DEFAULTS)
    if unknown:
        raise ValueError(f"unknown limit: {sorted(unknown)[0]}")

    result = {}
    for key, value in values.items():
        if isinstance(value, bool):
            raise ValueError(f"invalid {key}")
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError):
            raise ValueError(f"invalid {key}") from None
        if not isfinite(number):
            raise ValueError(f"invalid {key}")

        if key == "session_timeout_minutes":
            if number != int(number) or not 1 <= number <= 1440:
                raise ValueError("session_timeout_minutes must be 1..1440")
            result[key] = int(number)
        elif key == "freeze_days_step":
            if number != int(number) or not 1 <= number <= 30:
                raise ValueError("freeze_days_step must be 1..30")
            result[key] = int(number)
        elif key == "subscription_days":
            if number != int(number) or not 1 <= number <= 365:
                raise ValueError("subscription_days must be 1..365")
            result[key] = int(number)
        else:
            if number < 0 or number > 100000:
                raise ValueError("freeze_price_per_day must be 0..100000")
            result[key] = number
    return result


def effective_limits(values: dict | None) -> dict:
    current = dict(LIMIT_DEFAULTS)
    if isinstance(values, dict):
        try:
            current.update(normalize_limit_updates(values))
        except ValueError:
            # Keep old malformed records readable without propagating bad data.
            pass
    return current
