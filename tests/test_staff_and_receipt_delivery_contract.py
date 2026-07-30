from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_staff_hire_and_fire_send_direct_messages_after_commit():
    source = (ROOT / "handlers/admin_option.py").read_text(encoding="utf-8")
    hire_start = source.index("async def staff_add_role")
    hire = source[hire_start:]
    assert "await session.commit()" in hire
    assert "callback.bot.send_message(data[\"staff_telegram_id\"]" in hire
    assert "Вы приняты в команду клуба" in hire
    fire = source[source.index("async def staff_delete"):source.index("@router.callback_query", source.index("async def staff_delete") + 10)]
    assert "await session.commit()" in fire
    assert "callback.bot.send_message(staff.telegram_id" in fire
    assert "Вы уволены из клуба" in fire


def test_cash_product_receipt_targets_selected_athletes_all_parents_and_cash_staff():
    source = (ROOT / "admin_module/webapp_views.py").read_text(encoding="utf-8")
    sale_start = source.index("async def admin_product_sale(")
    block = source[sale_start:source.index("@router", sale_start + 10)]
    assert "selected_parent_ids = await get_student_parent_ids" in block
    assert "for parent_id in (selected_parent_ids or [buyer_user_id])" in block
    assert "await notify_product_staff" in block
    assert 'method="cash"' in block


def test_requisites_confirmation_and_decline_notify_selected_order_user():
    source = (ROOT / "handlers/manual_payment_review.py").read_text(encoding="utf-8")
    assert source.count("if order.user_id:") >= 4
    assert "await bot.send_message(order.user_id, user_text" in source
    assert "Оплата по реквизитам отклонена" in source
