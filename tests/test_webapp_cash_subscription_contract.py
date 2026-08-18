from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_webapp_cash_subscription_is_separate_from_bot_and_uses_manager_permission():
    api = (ROOT / "admin_module/api.py").read_text(encoding="utf-8")
    page = (ROOT / "templates/admin_cash_subscription.html").read_text(encoding="utf-8")
    buttons = (ROOT / "handlers/buttons.py").read_text(encoding="utf-8")
    assert '@router.get("/webapp/admin-cash-subscription"' in api
    assert '@router.post("/webapp/admin-cash-subscription"' in api
    assert 'verify_webapp_staff(club, init_data, db, "cash_view")' in api
    assert 'verify_webapp_staff(club, payload.init_data, db, "cash_view")' in api
    assert 'provider_payment_id=f"CASH:{order_id}"' in api
    assert "await add_abon" in api
    assert "/webapp/admin-cash-subscription" in buttons
    assert "Поиск атлета" in page or "Поиск по имени" in page
    assert "до ${until}" in page or "Продано до" in page


def test_cashier_does_not_have_cash_view_permission():
    permissions = (ROOT / "services/staff_permissions.py").read_text(encoding="utf-8")
    assert '"cashier": {"cash_sale", "products_view", "products_manage", "forecast_view"}' in permissions
    assert '"manager": {"cash_sale", "products_view", "products_manage", "cash_view"' in permissions
