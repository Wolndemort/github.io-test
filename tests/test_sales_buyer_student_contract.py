from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_sales_journal_shows_payer_and_student_for_subscription_orders():
    api = (ROOT / "admin_module/api.py").read_text(encoding="utf-8")
    template = (ROOT / "templates/admin_sales.html").read_text(encoding="utf-8")
    assert "order.student_id" in api
    assert "Кто купил" in api
    assert "Для кого" in api
    assert "item.buyer_label|safe" in template
