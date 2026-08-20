from pathlib import Path


def test_payment_webhook_verifies_provider_status_amount_metadata_and_duplicate_state():
    source = Path("admin_module/payments_webhook.py").read_text(encoding="utf-8")
    start = source.index('@router.post("/v1/payments/yookassa/webhook")')
    block = source[start:]
    for value in ('event != "payment.succeeded"', 'object_data.get("status") != "succeeded"', 'metadata.get("order_id")', 'order.status == "CONFIRMED"', 'with_for_update()', 'vp.get("metadata", {}).get("order_id")', 'amount != cart.amount_kopecks', 'cart.status = "CONFIRMED"'):
        assert value in block
