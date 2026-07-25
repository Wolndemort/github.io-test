from pathlib import Path


def test_bot_manage_subscription_exposes_delete_card_flow():
    source = Path("handlers/payments.py").read_text(encoding="utf-8")
    assert "callback_data='manage_subscription'" in source or 'callback_data="manage_subscription"' in source
    assert "callback_data=\"confirm_delete_card\"" in source
    assert "callback_data=\"execute_delete_card\"" in source
    assert "sub.rebill_id = None" in source
    assert "sub.is_active = False" not in source


def test_webapp_checkout_routes_use_saved_card_one_click_when_available():
    api = Path("admin_module/api.py").read_text(encoding="utf-8")
    cabinet = Path("admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")

    assert "payment_method_type=payment_method" in api
    assert "payment_method_type=payment_method" in cabinet
    assert "charge_payment(" in api
    assert "charge_payment(" in cabinet
    assert "Subscription.rebill_id.is_not(None)" in api
    assert "Subscription.rebill_id.is_not(None)" in cabinet


def test_webapp_payment_screens_expose_payment_method_selector():
    shop = Path("templates/shop.html").read_text(encoding="utf-8")
    cart = Path("templates/cart.html").read_text(encoding="utf-8")
    sub = Path("templates/webapp_buy_subscription.html").read_text(encoding="utf-8")
    freeze = Path("templates/webapp_buy_freeze.html").read_text(encoding="utf-8")

    for page in (shop, cart, sub, freeze):
        assert "payment_method" in page
        assert "sbp" in page
        assert "bank_card" in page
