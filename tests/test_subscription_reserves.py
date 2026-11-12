from datetime import date, timedelta

from services.subscription_reserves import (
    UNLIMITED,
    decide_purchase,
    queue_start,
    remaining_days,
    activate_reserve_start,
    upgrade_balance,
    upgrade_keeps_expiry,
)
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_finite_renewal_accumulates_instead_of_reserving():
    decision = decide_purchase(
        current_expire=date(2026, 11, 16), current_balance=3,
        new_lessons=8, order_id="finite", today=date(2026, 11, 3), days_to_add=30,
    )
    assert decision.status == "ACTIVE"
    assert decision.reason == "finite_plan_accumulates"


def test_unlimited_payment_is_paid_but_reserved():
    decision = decide_purchase(
        current_expire=date(2026, 11, 20), current_balance=12,
        new_lessons=UNLIMITED, order_id="unlimited", today=date(2026, 11, 3),
    )
    assert decision.status == "RESERVED"
    assert decision.scheduled_start == date(2026, 11, 21)
    assert decision.upgrade_available is True


def test_expired_subscription_does_not_create_reserve():
    decision = decide_purchase(
        current_expire=date(2026, 11, 2), current_balance=2,
        new_lessons=UNLIMITED, order_id="expired", today=date(2026, 11, 3),
    )
    assert decision.status == "ACTIVE"


def test_exhausted_subscription_does_not_create_reserve_even_before_expiry():
    decision = decide_purchase(
        current_expire=date(2026, 11, 20), current_balance=0,
        new_lessons=UNLIMITED, order_id="exhausted", today=date(2026, 11, 3),
    )
    assert decision.status == "ACTIVE"
    assert decision.reason == "no_active_subscription"
    assert decision.scheduled_start is None


def test_queue_handles_many_purchases_without_overlap():
    current = date(2026, 11, 10)
    queued = [(date(2026, 11, 11), 30), (date(2026, 12, 11), 30)]
    assert queue_start(current, queued, date(2026, 11, 3)) == date(2027, 1, 10)


def test_upgrade_changes_lessons_but_not_expiry():
    expiry = date(2026, 11, 20)
    assert upgrade_keeps_expiry(expiry, 12, UNLIMITED)
    assert remaining_days(expiry, date(2026, 11, 3)) == 18
    assert activate_reserve_start(date(2026, 11, 3), date(2026, 11, 3))
    assert upgrade_balance(12, UNLIMITED) == UNLIMITED


def test_reserve_contract_is_wired_to_schema_scheduler_and_client_cabinet():
    model = (ROOT / "database/db.py").read_text(encoding="utf-8")
    migration = (ROOT / "migrations/versions/x7y8z9a0b1c2_add_subscription_reserves.py").read_text(encoding="utf-8")
    scheduler = (ROOT / "services/scheduler_jobs.py").read_text(encoding="utf-8")
    cabinet = (ROOT / "admin_module/webapp_client_cabinet.py").read_text(encoding="utf-8")
    template = (ROOT / "templates/client_cabinet.html").read_text(encoding="utf-8")
    assert "activation_status" in model and "scheduled_start" in model
    assert "payment_orders" in migration and "scheduled_start" in migration
    assert "activate_subscription_reserves" in scheduler
    assert "reserve-upgrade" in cabinet and "reserve-upgrade" in template
