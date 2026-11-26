from types import SimpleNamespace

from services.visit_cancellation import restore_cancelled_visit_balance


def test_cancelled_paid_visit_returns_one_lesson():
    student = SimpleNamespace(balance_lessons=0)
    restore_cancelled_visit_balance(student, [SimpleNamespace(source="gate")])
    assert student.balance_lessons == 1


def test_cancelled_advance_visit_does_not_return_paid_lesson():
    student = SimpleNamespace(balance_lessons=0)
    restore_cancelled_visit_balance(student, [SimpleNamespace(source="advance")])
    assert student.balance_lessons == 0


def test_cancelled_unlimited_visit_keeps_unlimited_balance():
    student = SimpleNamespace(balance_lessons=999)
    restore_cancelled_visit_balance(student, [SimpleNamespace(source="gate")])
    assert student.balance_lessons == 999
