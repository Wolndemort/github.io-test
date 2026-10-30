from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services.advance_visits import advance_config
from services.gate_control import process_athlete_gate_pass


def test_advance_config_is_fail_closed_and_bounded():
    config = advance_config({"limits": {"advance_visits": {
        "enabled": "false", "max_debt_days": "not-a-number", "max_debt_lessons": 999999,
    }}})
    assert config == {"enabled": False, "max_debt_days": 15, "max_debt_lessons": 999}


@pytest.mark.asyncio
async def test_gate_uses_latest_real_visit_when_legacy_cache_is_empty():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    student = SimpleNamespace(
        id=1, club_id=10, name="Test", parent_id=99,
        expire_date=now - timedelta(days=1), balance_lessons=0,
        last_visit=None, is_frozen=0, frozen_at=None, frozen_days=None,
        advance_debt_days=0, advance_debt_lessons=0,
    )
    club = SimpleNamespace(id=10, name="Club")
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: student),
            SimpleNamespace(scalar_one_or_none=lambda: club),
        ]),
        scalar=AsyncMock(return_value=now - timedelta(seconds=2)),
        commit=AsyncMock(), rollback=AsyncMock(), flush=AsyncMock(), add=Mock(),
    )

    result = await process_athlete_gate_pass(
        1, db, {"limits": {"advance_visits": {"enabled": True}}}, expected_club_id=10,
    )

    assert result["success"] is False
    assert "Не спамьте" in result["message"]
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_staff_gate_requires_explicit_advance_confirmation():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    student = SimpleNamespace(
        id=1, club_id=10, name="Test", parent_id=99,
        expire_date=now - timedelta(days=1), balance_lessons=0,
        last_visit=None, is_frozen=0, frozen_at=None, frozen_days=None,
        advance_debt_days=0, advance_debt_lessons=1,
    )
    club = SimpleNamespace(id=10, name="Club")
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: student),
            SimpleNamespace(scalar_one_or_none=lambda: club),
        ]),
        scalar=AsyncMock(return_value=None),
        commit=AsyncMock(), rollback=AsyncMock(), flush=AsyncMock(), add=Mock(),
    )

    result = await process_athlete_gate_pass(
        1, db, {"limits": {"advance_visits": {"enabled": True}}},
        expected_club_id=10, require_advance_confirmation=True,
    )

    assert result["success"] is False
    assert result["requires_confirmation"] is True
    assert "№2" in result["message"]
    db.commit.assert_not_awaited()
