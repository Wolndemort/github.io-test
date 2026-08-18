from pathlib import Path


def test_subscription_and_freeze_cart_lines_use_separate_discount_scopes():
    source = Path("admin_module/api.py").read_text(encoding="utf-8")
    assert '"subscriptions", student.id' in source
    assert '"freeze", student.id' in source


def test_cash_and_online_freeze_apply_freeze_discounts():
    api = Path("admin_module/api.py").read_text(encoding="utf-8")
    cabinet = Path("admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")
    assert 'active_discounts(db, club.id, parent_id, "freeze"' in cabinet
    assert 'apply_discounts(int(round(price_per_day * days * 100))' in cabinet
    assert 'active_discounts(db, club_id, parent_id, "freeze"' in cabinet
