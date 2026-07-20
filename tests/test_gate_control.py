from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services.gate_control import process_athlete_gate_pass


def make_db(student, club):
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: student),
                SimpleNamespace(scalar_one_or_none=lambda: club),
            ]
        ),
        commit=AsyncMock(),
        rollback=AsyncMock(),
        flush=AsyncMock(),
        add=Mock(),
    )
    return db


def make_student(**overrides):
    values = {
        "id": 1,
        "club_id": 10,
        "name": "Тестовый атлет",
        "parent_id": 99,
        "expire_date": datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
        "balance_lessons": 5,
        "last_visit": None,
        "is_frozen": 0,
        "frozen_at": None,
        "frozen_days": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_gate_rejects_student_from_another_club():
    student = make_student(club_id=20)
    db = make_db(student, None)

    result = await process_athlete_gate_pass(
        1, db, {}, expected_club_id=10
    )

    assert result == {"success": False, "message": "Атлет не найден в этом клубе."}
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_gate_rejects_expired_student_without_changing_balance():
    student = make_student(
        expire_date=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    )
    db = make_db(student, SimpleNamespace(id=10, name="Клуб"))

    result = await process_athlete_gate_pass(1, db, {}, expected_club_id=10)

    assert result["success"] is False
    assert "истек" in result["message"]
    assert student.balance_lessons == 5
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_gate_opens_new_session_and_logs_visit():
    student = make_student()
    db = make_db(student, SimpleNamespace(id=10, name="Клуб"))

    result = await process_athlete_gate_pass(1, db, {}, expected_club_id=10)

    assert result["success"] is True
    assert result["is_inside_session"] is False
    assert student.last_visit is not None
    db.commit.assert_awaited_once()
    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_gate_rejects_rapid_repeat():
    recent_visit = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=2)
    student = make_student(last_visit=recent_visit)
    db = make_db(student, SimpleNamespace(id=10, name="Клуб"))

    result = await process_athlete_gate_pass(1, db, {}, expected_club_id=10)

    assert result["success"] is False
    assert "Не спамьте" in result["message"]
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_gate_early_unfreeze_returns_paid_freeze_duration():
    student = make_student(
        is_frozen=1,
        frozen_at=datetime.now(timezone.utc).replace(tzinfo=None),
        frozen_days=30,
        expire_date=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=40),
    )
    db = make_db(student, SimpleNamespace(id=10, name="Клуб"))

    result = await process_athlete_gate_pass(
        1, db, {"limits": {"freeze_days_step": 7}}, expected_club_id=10
    )

    assert result["success"] is True
    assert result["returned_early_days"] == 30
    assert student.is_frozen == 0
    assert student.frozen_at is None
    assert student.frozen_days is None
    assert student.expire_date <= datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=11)


@pytest.mark.asyncio
async def test_gate_early_unfreeze_legacy_freeze_uses_club_step():
    student = make_student(
        is_frozen=1,
        frozen_at=datetime.now(timezone.utc).replace(tzinfo=None),
        frozen_days=None,
        expire_date=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=20),
    )
    db = make_db(student, SimpleNamespace(id=10, name="Клуб"))

    result = await process_athlete_gate_pass(
        1, db, {"limits": {"freeze_days_step": 7}}, expected_club_id=10
    )

    assert result["success"] is True
    assert result["returned_early_days"] == 7
