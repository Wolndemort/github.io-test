"""Единые правила доступности пользовательских способов оплаты и прохода."""
def payment_availability(settings: dict | None) -> dict[str, bool]:
    settings = settings or {}; payments = settings.get("payments", {}) or {}
    online = bool((settings.get("features", {}) or {}).get("online_payments", False))
    configured = bool(payments.get("yookassa_shop_id") and payments.get("yookassa_secret_key"))
    return {"online": online and configured, "sbp": online and configured and bool(payments.get("yookassa_sbp_enabled", True)), "requisites": True}

def turnstile_enabled(settings: dict | None) -> bool:
    return bool(((settings or {}).get("turnstile", {}) or {}).get("enabled", False))
