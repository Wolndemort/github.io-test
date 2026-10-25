from pathlib import Path
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest


def test_student_has_subscription_freeze_duration_snapshot():
    model = Path("database/db.py").read_text(encoding="utf-8")
    migration = Path("migrations/versions/ab2c3d4e5f6_add_freeze_days_entitlement.py").read_text(encoding="utf-8")
    assert "freeze_days_entitlement" in model
    assert 'sa.Column("freeze_days_entitlement", sa.Integer(), nullable=True)' in migration
    assert 'down_revision = "y7z8a9b0c1d2"' in migration
    assert "UPDATE students AS s" in migration
    assert "freeze_days_step" in migration
    assert "op.drop_column(\"students\", \"freeze_days_entitlement\")" in migration


def test_subscription_activation_snapshots_the_current_club_step():
    source = Path("database/db.py").read_text(encoding="utf-8")
    assert "student.freeze_days_entitlement" in source
    assert "configured_freeze_days" in source
    assert "max(1, min(365, int(configured_freeze_days)))" in source


def test_client_freeze_uses_the_subscription_snapshot_with_legacy_fallback():
    source = Path("handlers/user_option.py").read_text(encoding="utf-8")
    assert "freeze_days_entitlement" in source
    assert 'club_settings.get("limits", {}).get("freeze_days_step", 7)' in source
    assert "Срок заморозки указан в его абонементе" in source


def test_admin_students_show_entitlement_as_read_only_without_submitting_it_as_current_freeze():
    page = Path("templates/admin_students.html").read_text(encoding="utf-8")
    assert "s.freeze_days_entitlement" in page
    assert "s.frozen_days" not in page
    assert 'name="frozen_days"' not in page


@pytest.mark.asyncio
async def test_subscription_activation_keeps_the_step_used_at_purchase():
    from database.db import add_abon

    student = SimpleNamespace(
        id=1,
        club_id=10,
        name="Athlete",
        discipline="boxing",
        expire_date=datetime.now() + timedelta(days=1),
        balance_lessons=2,
        is_frozen=0,
        frozen_at=None,
        frozen_days=None,
        can_freeze=0,
        parent_id=None,
    )
    session = SimpleNamespace(get=AsyncMock(return_value=student), commit=AsyncMock(), rollback=AsyncMock())
    await add_abon(1, 8, session, 10, {"features": {"freeze": True}, "limits": {"freeze_days_step": 14}, "disciplines": {"boxing": {"type": "lessons"}}}, days_to_add=30, discipline="boxing")
    assert student.freeze_days_entitlement == 14
