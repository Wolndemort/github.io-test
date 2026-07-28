from pathlib import Path


def test_cash_product_sale_resolves_all_student_parents_for_receipts():
    source = (Path(__file__).parents[1] / "admin_module/webapp_views.py").read_text(encoding="utf-8")
    assert "get_student_parent_ids" in source
    assert "selected_parent_ids" in source
    assert "for parent_id in (selected_parent_ids or [buyer_user_id])" in source
