"""Rules for paid subscription reserves and unlimited upgrades.

This module deliberately contains the business decision separately from payment
providers and Telegram/WebApp handlers.  A payment is never rejected because a
student already has a subscription: it is either activated now or recorded as
a paid reserve.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta


EARLY_RENEWAL_DAYS = 15
UNLIMITED = 999


@dataclass(frozen=True)
class ReserveDecision:
    status: str  # ACTIVE or RESERVED
    scheduled_start: date | None
    parent_order_id: str | None
    upgrade_available: bool
    reason: str


def _day(value: datetime | date | None) -> date | None:
    if value is None:
        return None
    return value.date() if isinstance(value, datetime) else value


def remaining_days(expire_at: datetime | date | None, today: date | None = None) -> int:
    """Return inclusive calendar days still available, never negative."""
    end = _day(expire_at)
    today = today or date.today()
    return max(0, (end - today).days + 1) if end else 0


def queue_start(
    current_expire: datetime | date | None,
    queued: list[tuple[datetime | date, int]] | None = None,
    today: date | None = None,
) -> date:
    """Find the first free day after the current and all queued entitlements."""
    today = today or date.today()
    start = max(today, (_day(current_expire) + timedelta(days=1)) if current_expire else today)
    for queued_start, days in sorted(queued or [], key=lambda item: _day(item[0]) or today):
        queued_start = _day(queued_start) or today
        end = queued_start + timedelta(days=max(1, int(days or 0)) - 1)
        if end >= start:
            start = end + timedelta(days=1)
    return start


def decide_purchase(
    *,
    current_expire: datetime | date | None,
    current_balance: int,
    new_lessons: int,
    order_id: str,
    queued: list[tuple[datetime | date, int]] | None = None,
    today: date | None = None,
    days_to_add: int = 30,
) -> ReserveDecision:
    """Decide activation without rejecting or delaying payment.

    Finite plans continue to use the existing additive rule.  Unlimited plans
    are paid immediately but become a reserve, preventing the remaining finite
    days from silently turning into unlimited access.  The client/admin may
    explicitly choose the upgrade action to turn the current period unlimited.
    """
    today = today or date.today()
    left = remaining_days(current_expire, today)
    if left <= 0:
        return ReserveDecision("ACTIVE", None, None, False, "no_active_subscription")
    if int(new_lessons or 0) != UNLIMITED:
        return ReserveDecision("ACTIVE", None, None, False, "finite_plan_accumulates")

    start = queue_start(current_expire, queued, today)
    return ReserveDecision(
        "RESERVED",
        start,
        order_id,
        True,
        "unlimited_waits_for_current_subscription",
    )


def upgrade_keeps_expiry(current_expire: datetime | date, current_balance: int, new_lessons: int) -> bool:
    """Whether an explicit unlimited upgrade changes lessons only."""
    return bool(_day(current_expire) and int(new_lessons or 0) == UNLIMITED and int(current_balance or 0) != UNLIMITED)


def activate_reserve_start(scheduled_start: datetime | date, today: date | None = None) -> bool:
    """Whether a paid reserve is due for automatic activation."""
    return (_day(scheduled_start) or date.max) <= (today or date.today())


def upgrade_balance(current_balance: int, new_lessons: int) -> int:
    """Apply the explicit upgrade without changing the existing expiry date."""
    if int(new_lessons or 0) != UNLIMITED:
        raise ValueError("Only an unlimited tariff can be used as an upgrade")
    return UNLIMITED
