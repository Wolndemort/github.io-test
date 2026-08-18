from pathlib import Path

ROOT = Path(__file__).parents[1]

def test_web_cash_subscription_sends_receipt_to_parents_and_owner():
    source = (ROOT / "admin_module/api.py").read_text(encoding="utf-8")
    block = source[source.index("async def admin_cash_subscription("):source.index('@router.post("/admin/students")')]
    assert "get_student_parent_ids" in block
    assert "send_message(chat_id=parent_id, text=receipt" in block
    assert "club.owner_id" in block
    assert "await bot.session.close()" in block
    page = (ROOT / "templates/admin_cash_subscription.html").read_text(encoding="utf-8")
    assert "discount_id" in page
    assert "discounts" in page

def test_bot_cash_subscription_sends_receipt_to_parent_and_owner():
    source = (ROOT / "handlers/payments.py").read_text(encoding="utf-8")
    block = source[source.index("async def final_cash_pay"):source.index("async def process_manage_subscription")]
    assert "chat_id=parent_id" in block
    assert "chat_id=int(club.owner_id)" in block
