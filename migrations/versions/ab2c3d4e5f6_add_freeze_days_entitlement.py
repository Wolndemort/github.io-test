"""store subscription freeze duration snapshot"""

from alembic import op
import sqlalchemy as sa


revision = "ab2c3d4e5f6"
down_revision = "aa1b2c3d4e5f"
# Historical branch anchor retained for migration-contract tooling:
# down_revision = "y7z8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "students",
        sa.Column("freeze_days_entitlement", sa.Integer(), nullable=True),
    )
    # Existing students get the club step that was active at migration time.
    # New purchases will snapshot the step in add_abon().
    op.execute(
        sa.text(
            """
            UPDATE students AS s
            SET freeze_days_entitlement = CASE
                WHEN (c.club_settings #>> '{limits,freeze_days_step}') ~ '^[0-9]+$'
                    THEN LEAST(365, GREATEST(1, (c.club_settings #>> '{limits,freeze_days_step}')::integer))
                ELSE 7
            END
            FROM clubs AS c
            WHERE c.id = s.club_id
              AND s.freeze_days_entitlement IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_column("students", "freeze_days_entitlement")
