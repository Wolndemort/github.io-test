from pathlib import Path


def _webapp_create_block() -> str:
    source = Path("admin_module/api.py").read_text(encoding="utf-8")
    start = source.index("async def admin_create_student")
    end = source.find('@router.post("/admin/students/{student_id}')
    return source[start:] if end < 0 else source[start:end]


def test_webapp_student_creation_creates_cash_sale_for_selected_tariff():
    block = _webapp_create_block()
    assert "if payload.tariff_idx is not None:" in block
    assert "sale_order = PaymentOrder(" in block
    assert 'status="CONFIRMED"' in block
    assert 'provider_payment_id=f"CASH:ADMIN_' in block
    assert "student_id=new_student.id" in block
    assert "amount_kopecks=price_kopecks" in block


def test_telegram_manual_student_creation_creates_cash_sale_for_selected_tariff():
    source = Path("handlers/admin_students.py").read_text(encoding="utf-8")
    start = source.index("async def _finish_manual_add")
    end = source.index('@router.callback_query(F.data.startswith("admin_manual_tariff_")')
    block = source[start:end]
    assert "if tariff_idx is not None:" in block
    assert "session.add(PaymentOrder(" in block
    assert 'status="CONFIRMED"' in block
    assert 'provider_payment_id=f"CASH:ADMIN_' in block
    assert "student_id=new_student.id" in block
    assert "amount_kopecks=price_kopecks" in block


def test_student_without_subscription_keeps_payment_creation_inside_tariff_guard():
    block = _webapp_create_block()
    guard_start = block.index("if payload.tariff_idx is not None:")
    guard = block[guard_start:block.index("await db.commit()", guard_start)]
    assert "PaymentOrder(" in guard


def test_telegram_manual_student_creation_is_club_serialized_and_duplicate_safe():
    source = Path("handlers/admin_students.py").read_text(encoding="utf-8")
    start = source.index("async def _finish_manual_add")
    end = source.index('@router.callback_query(F.data.startswith("admin_manual_tariff_")')
    block = source[start:end]
    assert "with_for_update()" in block
    assert "except IntegrityError" in block
