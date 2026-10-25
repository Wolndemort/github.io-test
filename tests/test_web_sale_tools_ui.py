from pathlib import Path


def test_native_staff_sales_page_has_cash_subscription_and_product_sale():
    source = Path('static/web/sale-tools.js').read_text(encoding='utf-8')
    for endpoint in ('/api/v1/staff/sales/options', '/api/v1/staff/sales/buyers', '/api/v1/staff/sales/cash-subscription', '/api/v1/staff/sales/cash-product'):
        assert endpoint in source
    assert 'discount_ids' in source and 'X-CSRF-Token' in source and 'idempotency_key' in source
