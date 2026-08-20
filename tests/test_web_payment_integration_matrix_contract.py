from pathlib import Path


def test_web_payment_webhook_matrix_has_provider_retry_and_verified_currency_guards():
    source = Path("admin_module/payments_webhook.py").read_text(encoding="utf-8")
    start = source.index('@router.post("/v1/payments/yookassa/webhook")')
    block = source[start:]
    assert block.count('"status": "retry"') >= 3
    assert block.count('get("currency") != "RUB"') >= 2
    assert 'vp.get("metadata", {}).get("order_id") != order_id' in block
    assert 'verified_payment.get("metadata", {}).get("order_id") != str(order.id)' in block
    assert 'order.status == "CONFIRMED"' in block
    assert 'amount != cart.amount_kopecks' in block
    assert 'received_amount != order.amount_kopecks' in block
