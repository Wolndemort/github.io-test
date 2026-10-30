from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services.advance_visits import advance_preview, record_advance_visit, settle_advance_debt, advance_purchase_warning, advance_profile_notice
from services.gate_control import process_athlete_gate_pass


def student(**changes):
    data = {
        "id": 1,
        "club_id": 10,
        "name": "Тест",
        "parent_id": 99,
        "expire_date": datetime(2026, 10, 1, 23, 59, 59),
        "balance_lessons": 0,
        "last_visit": None,
        "is_frozen": 0,
        "frozen_at": None,
        "frozen_days": None,
        "advance_debt_days": 0,
        "advance_debt_lessons": 0,
    }
    data.update(changes)
    return SimpleNamespace(**data)


def db_for(item):
    return SimpleNamespace(
        execute=AsyncMock(side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: item),
            SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(id=10, name="Клуб")),
        ]),
        commit=AsyncMock(), rollback=AsyncMock(), flush=AsyncMock(), add=Mock(),
    )


def settings(enabled=True, days=15, lessons=7):
    return {"limits": {"advance_visits": {"enabled": enabled, "max_debt_days": days, "max_debt_lessons": lessons}}}


def test_preview_counts_overdue_days_and_next_lesson_without_mutating():
    item = student()
    result = advance_preview(item, datetime(2026, 10, 11, 12), settings(), new_session=True)
    assert result["is_advance"] is True
    assert result["debt_days"] == 1
    assert result["debt_lessons"] == 1
    assert item.advance_debt_days == 0


def test_record_and_settle_debt_consumes_days_and_lessons():
    item = student()
    record_advance_visit(item, datetime(2026, 10, 11, 12), settings())
    assert (item.advance_debt_days, item.advance_debt_lessons) == (1, 1)
    result = settle_advance_debt(item, 30, 8, settings())
    assert result["days_consumed"] == 1
    assert result["lessons_consumed"] == 1
    assert (item.advance_debt_days, item.advance_debt_lessons) == (0, 0)


def test_purchase_warning_is_human_and_existing_debt_survives_feature_toggle():
    item = student(advance_debt_days=4, advance_debt_lessons=2)
    assert "4" in advance_purchase_warning(item, settings())
    assert "2" in advance_purchase_warning(item, settings())
    assert "4" in advance_purchase_warning(item, settings(enabled=False))
    result = settle_advance_debt(item, 30, 8, settings(enabled=False))
    assert result["days_consumed"] == 4
    assert result["lessons_consumed"] == 2
    assert (item.advance_debt_days, item.advance_debt_lessons) == (0, 0)


def test_profile_notice_replaces_balance_only_while_debt_exists():
    assert advance_profile_notice(student()) == ""
    notice = advance_profile_notice(student(advance_debt_days=4, advance_debt_lessons=2))
    assert "Посещения в долг: 2" in notice
    assert "спишется <b>2</b> занятия" in notice
    assert "учтётся <b>4</b> дня срока" in notice


def test_debt_days_follow_first_and_latest_real_debt_visits_not_expiry():
    item = student(expire_date=datetime(2026, 10, 1, 23, 59, 59))
    first = advance_preview(item, datetime(2026, 10, 20, 10), settings(), first_advance_at=None)
    assert first["debt_days"] == 1
    item.advance_debt_days = first["debt_days"]
    item.advance_debt_lessons = first["debt_lessons"]
    later = advance_preview(
        item,
        datetime(2026, 10, 23, 10),
        settings(),
        first_advance_at=datetime(2026, 10, 20, 10),
    )
    assert later["debt_days"] == 4


def test_limited_package_leaves_unpaid_debt_and_unlimited_clears_only_days():
    item = student(advance_debt_days=20, advance_debt_lessons=10)
    result = settle_advance_debt(item, 5, 3, settings())
    assert result["days_consumed"] == 5
    assert result["lessons_consumed"] == 3
    assert (item.advance_debt_days, item.advance_debt_lessons) == (15, 7)

    unlimited = student(advance_debt_days=20, advance_debt_lessons=10)
    result = settle_advance_debt(unlimited, 5, 999, settings())
    assert result["days_consumed"] == 5
    assert result["lessons_consumed"] == 10
    assert (unlimited.advance_debt_days, unlimited.advance_debt_lessons) == (15, 0)


@pytest.mark.asyncio
async def test_gate_allows_advance_visit_and_persists_debt():
    item = student(expire_date=datetime.now() - timedelta(days=1))
    db = db_for(item)
    result = await process_athlete_gate_pass(1, db, settings(), expected_club_id=10)
    assert result["success"] is True
    assert result["is_advance_visit"] is True
    assert item.advance_debt_lessons == 1
    assert db.commit.await_count == 1


@pytest.mark.asyncio
async def test_gate_rejects_when_advance_lesson_limit_is_reached():
    item = student(expire_date=datetime.now() - timedelta(days=1), advance_debt_lessons=7)
    db = db_for(item)
    result = await process_athlete_gate_pass(1, db, settings(lessons=7), expected_club_id=10)
    assert result["success"] is False
    assert "Р›РёРјРёС‚" in result["message"]
    db.commit.assert_not_awaited()
