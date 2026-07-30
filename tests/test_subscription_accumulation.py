from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from database.db import add_abon


def make_session(student):
    return SimpleNamespace(get=AsyncMock(return_value=student), commit=AsyncMock(), rollback=AsyncMock())


def make_student(**overrides):
    values = {
        "name": "Тестовый атлет",
        "parent_id": 99,
        "club_id": 2,
        "expire_date": datetime(2026, 8, 10, 23, 59, 59),
        "balance_lessons": 5,
        "discipline": "boxing",
        "is_frozen": 0,
        "frozen_at": None,
        "frozen_days": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_active_subscription_adds_lessons_and_extends_expiry():
    student = make_student()
    result = await add_abon(
        student_id=1,
        lessons_count=8,
        session=make_session(student),
        club_id=2,
        club_settings={"disciplines": {"boxing": {"type": "lessons"}}, "features": {"freeze": True}},
        days_to_add=30,
        discipline="boxing",
    )

    assert student.balance_lessons == 13
    assert student.expire_date == datetime(2026, 9, 9, 23, 59, 59)
    assert result[0] == "09.09.2026"


@pytest.mark.asyncio
async def test_expired_subscription_starts_new_balance_and_period():
    student = make_student(expire_date=datetime.utcnow() - timedelta(days=1), balance_lessons=5)
    await add_abon(
        student_id=1,
        lessons_count=8,
        session=make_session(student),
        club_id=2,
        club_settings={"disciplines": {"boxing": {"type": "lessons"}}, "features": {"freeze": True}},
        days_to_add=30,
        discipline="boxing",
    )

    assert student.balance_lessons == 8
    assert student.expire_date.hour == 23
    assert student.expire_date.minute == 59


@pytest.mark.asyncio
async def test_finite_tariff_replaces_existing_unlimited_balance():
    student = make_student(balance_lessons=999)
    await add_abon(
        student_id=1,
        lessons_count=8,
        session=make_session(student),
        club_id=2,
        club_settings={"disciplines": {"boxing": {"type": "lessons"}}, "features": {"freeze": True}},
        days_to_add=30,
        discipline="boxing",
    )

    assert student.balance_lessons == 8


@pytest.mark.asyncio
async def test_two_purchases_while_frozen_extend_existing_date_and_sum_lessons():
    student = make_student(
        expire_date=datetime(2026, 8, 17, 23, 59, 59),
        balance_lessons=5,
        is_frozen=1,
        frozen_at=datetime(2026, 7, 30, 12, 0, 0),
        frozen_days=7,
    )
    settings = {"disciplines": {"boxing": {"type": "lessons"}}, "features": {"freeze": True}}
    await add_abon(1, 8, make_session(student), 2, settings, days_to_add=30, discipline="boxing")
    assert student.is_frozen == 0
    assert student.frozen_at is None
    assert student.frozen_days is None
    first_expire = student.expire_date
    await add_abon(1, 12, make_session(student), 2, settings, days_to_add=30, discipline="boxing")
    assert student.balance_lessons == 25
    assert student.expire_date == first_expire + timedelta(days=30)
