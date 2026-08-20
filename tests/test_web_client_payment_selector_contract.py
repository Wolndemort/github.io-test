from pathlib import Path


def test_client_payment_ui_uses_scoped_pending_order_selector():
    source = Path("static/web/components.js").read_text(encoding="utf-8")
    api = Path("auth/forecast_routes.py").read_text(encoding="utf-8")
    assert 'name="order_id" required><option value="">Loading orders' in source
    assert "/api/v1/client/purchases/data" in source
    assert "payable_orders" in api
    assert 'CartOrder.status == "NEW"' in api
    assert "CartOrder.club_id == actor.club_id" in api
    assert "CartOrder.user_id == actor.user_id" in api
