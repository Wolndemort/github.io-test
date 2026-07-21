from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from database.db import process_student_freeze, purchase_student_freeze
from services.gate_control import process_athlete_gate_pass


def make_session(student):
    return SimpleNamespace(
        get=AsyncMock(return_value=student),
        commit=AsyncMock(),
        rollback=AsyncMock(),
        flush=AsyncMock(),
        execute=AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: student),
                SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(id=10, name="Клуб")),
            ]
        ),
        add=AsyncMock(),
    )


def make_student(**overrides):
    values = {
        "id": 1,
        "club_id": 10,
        "name": "Тестовый атлет",
        "parent_id": 99,
        "expire_date": datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
        "balance_lessons": 5,
        "last_visit": None,
        "can_freeze": 1,
        "is_frozen": 0,
        "frozen_at": None,
        "frozen_days": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_free_freeze_consumes_limit_and_marks_student_frozen():
    student = make_student()
    session = make_session(student)

    result = await process_student_freeze(
        student_id=1,
        club_id=10,
        club_settings={"features": {"freeze": True}},
        session=session,
        days=7,
    )

    assert result is not None
    assert student.is_frozen == 1
    assert student.can_freeze == 0
    assert student.frozen_days == 7
    assert student.frozen_at is not None
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_paid_freeze_adds_days_and_sets_freeze_state_without_consuming_free_limit():
    student = make_student(can_freeze=1)
    session = make_session(student)

    result = await purchase_student_freeze(
        student_id=1,
        club_id=10,
        days=14,
        session=session,
    )

    assert result is not None
    assert student.is_frozen == 1
    assert student.frozen_days == 14
    assert student.can_freeze == 1
    assert student.expire_date.date() >= (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=44)).date()
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_paid_freeze_rejects_expired_student():
    student = make_student(
        expire_date=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    )
    session = make_session(student)

    result = await purchase_student_freeze(
        student_id=1,
        club_id=10,
        days=14,
        session=session,
    )

    assert result is None
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_paid_freeze_then_gate_pass_reduces_expire_by_paid_days():
    student = make_student(
        expire_date=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=40),
        frozen_days=14,
        is_frozen=1,
        frozen_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    session = make_session(student)

    gate_result = await process_athlete_gate_pass(
        student_id=1,
        db=session,
        club_settings={"limits": {"freeze_days_step": 7}},
        expected_club_id=10,
    )

    assert gate_result["success"] is True
    assert gate_result["is_was_frozen"] is True
    assert gate_result["returned_early_days"] == 14
    assert student.is_frozen == 0
    assert student.frozen_days is None
    assert student.frozen_at is None
    assert student.expire_date.date() <= (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=26)).date()
