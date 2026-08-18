from pathlib import Path


def test_cash_register_counts_all_non_cash_payment_methods_as_online():
    source = Path("admin_module/api.py").read_text(encoding="utf-8")
    assert 'r.get("method") in {"card", "sbp", "requisites"}' in source


def test_operations_allocate_cart_order_total_instead_of_ignoring_discounts():
    source = Path("admin_module/api.py").read_text(encoding="utf-8")
    assert "operation_amount = int(order.amount_kopecks or 0) - allocated" in source
    assert "raw_total = sum" in source
