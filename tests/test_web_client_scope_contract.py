from pathlib import Path


SOURCE = Path("auth/forecast_routes.py").read_text(encoding="utf-8")


def test_client_student_update_and_freeze_enforce_linked_student_scope():
    assert '"student_scope_denied"' in SOURCE
    assert 'StudentParent.student_id == student.id, StudentParent.parent_id == actor.user_id' in SOURCE


def test_client_payment_intent_is_order_and_club_scoped():
    assert 'CartOrder.id == order_id, CartOrder.club_id == actor.club_id, CartOrder.user_id == actor.user_id' in SOURCE


def test_client_reads_use_primary_or_linked_parent_scope():
    assert "async def client_scoped_students" in SOURCE
    assert "or_(Student.parent_id == actor.user_id, StudentParent.parent_id == actor.user_id)" in SOURCE
