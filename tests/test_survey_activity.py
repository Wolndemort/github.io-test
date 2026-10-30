from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.surveys import _last_activity


@pytest.mark.asyncio
async def test_survey_activity_uses_latest_visit_log_over_student_cache():
    student = SimpleNamespace(id=7, club_id=2, last_visit=None, created_at=datetime(2020, 1, 1))
    session = SimpleNamespace(scalar=AsyncMock(return_value=datetime(2026, 10, 29, 12, 30)))

    assert await _last_activity(session, student) == datetime(2026, 10, 29, 12, 30)
    session.scalar.assert_awaited_once()


@pytest.mark.asyncio
async def test_survey_activity_does_not_treat_creation_as_a_visit():
    student = SimpleNamespace(id=7, club_id=2, last_visit=None, created_at=datetime(2020, 1, 1))
    session = SimpleNamespace(scalar=AsyncMock(return_value=None))

    assert await _last_activity(session, student) is None


@pytest.mark.asyncio
async def test_survey_activity_keeps_legacy_cache_as_fallback():
    cached_visit = datetime(2026, 10, 1, 9, 0)
    student = SimpleNamespace(id=7, club_id=2, last_visit=cached_visit, created_at=None)
    session = SimpleNamespace(scalar=AsyncMock(return_value=None))

    assert await _last_activity(session, student) == cached_visit
