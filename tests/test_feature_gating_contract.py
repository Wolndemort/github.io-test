from pathlib import Path

from services.availability import payment_availability, turnstile_enabled


ROOT = Path(__file__).parents[1]


def _settings(*, online=False, configured=False, sbp=True, freeze=True, turnstile=False):
    payments = {"yookassa_sbp_enabled": sbp}
    if configured:
        payments.update({"yookassa_shop_id": "shop", "yookassa_secret_key": "secret"})
    return {
        "features": {"online_payments": online, "freeze": freeze},
        "payments": payments,
        "turnstile": {"enabled": turnstile},
    }


def test_payment_and_turnstile_availability_matrix():
    assert payment_availability(_settings()) == {"online": False, "sbp": False, "requisites": True}
    assert payment_availability(_settings(online=True, configured=True)) == {"online": True, "sbp": True, "requisites": True}
    assert payment_availability(_settings(online=True, configured=True, sbp=False))["sbp"] is False
    assert turnstile_enabled(_settings()) is False
    assert turnstile_enabled(_settings(turnstile=True)) is True


def test_all_purchase_paths_check_feature_and_payment_flags():
    cabinet = (ROOT / "admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")
    api = (ROOT / "admin_module/api.py").read_text(encoding="utf-8")
    assert 'features", {}).get("freeze", True)' in cabinet
    assert 'not discipline_cfg.get("active", True)' in cabinet
    assert 'not cfg.get("active", True)' in api
    assert 'not settings.get("features", {}).get("freeze", True)' in api
    assert cabinet.count('payment_method in {"bank_card", "sbp"} and not payment_modes["online"]') >= 2
    assert 'payment_method in {"bank_card", "sbp"} and not payment_modes["online"]' in api


def test_turnstile_and_qr_fallback_are_gated_end_to_end():
    buttons = (ROOT / "handlers/buttons.py").read_text(encoding="utf-8")
    cabinet = (ROOT / "admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")
    api = (ROOT / "admin_module/api.py").read_text(encoding="utf-8")
    assert "turnstile_enabled(club_settings)" in buttons
    assert "Турникет отключён администратором" in cabinet
    assert "Посещение отмечено." in api
    assert 'features", {}).get("qr_checkin", True)' in api


def test_new_club_defaults_are_isolated_and_safe():
    constants = (ROOT / "database/constants.py").read_text(encoding="utf-8")
    db = (ROOT / "database/db.py").read_text(encoding="utf-8")
    super_admin = (ROOT / "handlers/super_admin_handlers.py").read_text(encoding="utf-8")
    assert '"online_payments": False' in constants
    assert '"enabled": False' in constants
    assert "copy.deepcopy(DEFAULT_CLUB_SETTINGS)" in db
    assert "copy.deepcopy(DEFAULT_CLUB_SETTINGS)" in super_admin


def test_motivation_keeps_logic_but_has_safe_unavailable_fallback():
    views = (ROOT / "admin_module/webapp_views.py").read_text(encoding="utf-8")
    fallback = (ROOT / "templates/motivation_unavailable.html").read_text(encoding="utf-8")
    assert 'motivation_unavailable.html' in views
    assert 'Раздел временно находится в доработке' in fallback
    assert 'async def save_admin_motivation_rules' in views
