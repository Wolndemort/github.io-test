from types import SimpleNamespace

from admin_module.webapp_views import _product_financials, _product_totals


def product(price, purchase, stock):
    return SimpleNamespace(price_kopecks=price, purchase_price_kopecks=purchase, stock=stock)


def test_product_financials_use_stock_and_keep_kopecks_exact():
    assert _product_financials(product(12500, 7000, 3)) == {
        "stock_value_kopecks": 37500,
        "potential_profit_kopecks": 16500,
    }


def test_product_totals_sum_sale_cost_and_profit_for_all_products():
    assert _product_totals([product(10000, 6000, 2), product(2500, 1500, 4)]) == {
        "stock_value_kopecks": 30000,
        "potential_profit_kopecks": 12000,
        "purchase_value_kopecks": 18000,
    }


def test_negative_stock_does_not_create_negative_inventory_value():
    assert _product_financials(product(10000, 6000, -2)) == {
        "stock_value_kopecks": 0,
        "potential_profit_kopecks": 0,
    }
