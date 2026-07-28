import ast
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_bot_manual_confirmation_derives_payment_fields_locally():
    source = (ROOT / "handlers/payments.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "admin_confirm_payment")
    assigned = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    assert {"selected_tariff", "amount_kopecks", "target_discipline"} <= assigned
    assert "CASH_SUB_" in source
    assert 'status="CONFIRMED"' in source


def test_webapp_requisites_create_pending_payment_and_owner_review():
    source = (ROOT / "admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")
    assert 'payment_method == "requisites"' in source
    assert 'status="NEW"' in source
    assert 'provider_payment_id=f"MANUAL:{order_id}"' in source
    assert "_manual_review_keyboard(order_id)" in source
    assert "После перевода админ подтвердит заявку вручную." in source


def test_manual_review_is_owner_only_and_idempotent_for_both_order_types():
    source = (ROOT / "handlers/manual_payment_review.py").read_text(encoding="utf-8")
    assert "callback.from_user.id != club.owner_id" in source
    assert 'order.status != "NEW"' in source
    assert "order.status = \"CONFIRMED\"" in source
    assert "order.status = \"REJECTED\"" in source
    assert "manual_order_confirmed" in source
    assert "manual_order_declined" in source


def test_yookassa_webhook_verifies_provider_status_amount_and_order_metadata():
    source = (ROOT / "admin_module/payments_webhook.py").read_text(encoding="utf-8")
    assert 'verified_payment.get("status") != "succeeded"' in source
    assert 'verified_payment.get("metadata", {}).get("order_id")' in source
    assert "received_amount != order.amount_kopecks" in source
    assert 'if order.status == "CONFIRMED"' in source
