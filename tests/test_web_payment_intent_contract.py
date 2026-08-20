from pathlib import Path


def test_web_payment_intent_is_server_priced_scoped_and_provider_safe():
    source = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    start = source.index('@client_router.post("/payments/{order_id}/intent")'); block = source[start:]
    for value in ("WEB_ONLINE_PAYMENTS_ENABLED", "require_csrf", "CartOrder.id == order_id", "CartOrder.club_id == actor.club_id", "CartOrder.user_id == actor.user_id", "with_for_update()", "order.status != \"NEW\"", "yookassa_shop_id", "payment_provider_unavailable", "YooKassaClient", "web_payment_intent_created"):
        assert value in block
    assert "amount_kopecks" in block
