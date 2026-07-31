from pathlib import Path

from services.availability import payment_availability

ROOT = Path(__file__).parents[1]


def settings(*, online=True, sbp=True, configured=True):
    payments = {"yookassa_sbp_enabled": sbp}
    if configured:
        payments.update({"yookassa_shop_id": "shop", "yookassa_secret_key": "secret"})
    return {"features": {"online_payments": online}, "payments": payments}


def test_online_payment_matrix_requires_feature_and_yookassa_credentials():
    assert payment_availability(settings()) == {"online": True, "sbp": True, "requisites": True}
    assert payment_availability(settings(online=False)) == {"online": False, "sbp": False, "requisites": True}
    assert payment_availability(settings(configured=False)) == {"online": False, "sbp": False, "requisites": True}
    assert payment_availability(settings(sbp=False)) == {"online": True, "sbp": False, "requisites": True}


def test_all_webapp_purchase_pages_use_the_same_availability_context():
    cabinet = (ROOT / "admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")
    views = (ROOT / "admin_module/webapp_views.py").read_text(encoding="utf-8")
    freeze = (ROOT / "templates/webapp_buy_freeze.html").read_text(encoding="utf-8")
    subscription = (ROOT / "templates/webapp_buy_subscription.html").read_text(encoding="utf-8")
    cart = (ROOT / "templates/cart.html").read_text(encoding="utf-8")
    shop = (ROOT / "templates/shop.html").read_text(encoding="utf-8")
    assert cabinet.count("payment_availability") >= 3
    assert '"online_enabled": payment_modes["online"]' in cabinet
    assert '"online_enabled": payment_modes["online"]' in views
    for page in (freeze, subscription, cart):
        assert "{% if online_enabled %}" in page
        assert "{% if sbp_enabled %}" in page
    assert "hideUnavailablePaymentButtons" in shop


def test_online_methods_are_rejected_server_side_for_subscription_and_freeze():
    source = (ROOT / "admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")
    assert source.count('payment_method in {"bank_card", "sbp"}') >= 2
    assert source.count('not payment_modes["online"]') >= 2


def test_bot_removes_online_buttons_but_keeps_manual_requisites_fallback():
    source = (ROOT / "handlers/payments.py").read_text(encoding="utf-8")
    assert 'payment_availability(club_settings)["online"]' in source
    assert '"pay_method_sbp_yookassa"' in source
    assert '"pay_method_official"' in source
    assert '"pay_method_sbp"' in source
