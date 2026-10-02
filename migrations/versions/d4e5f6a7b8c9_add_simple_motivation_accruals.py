"""add the simple attendance-based motivation result table"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "motivation_accruals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("club_id", sa.Integer(), nullable=False),
        sa.Column("discipline", sa.String(50), nullable=False),
        sa.Column("occurrence_key", sa.String(180), nullable=False),
        sa.Column("occurrence_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.String(5), nullable=False),
        sa.Column("student_count", sa.Integer(), nullable=False),
        sa.Column("rule_min_students", sa.Integer(), nullable=False),
        sa.Column("rule_max_students", sa.Integer(), nullable=False),
        sa.Column("rate_kopecks", sa.Integer(), nullable=False),
        sa.Column("staff_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["club_id"], ["clubs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("occurrence_key"),
    )
    op.create_index("ix_motivation_accruals_club_id", "motivation_accruals", ["club_id"])
    op.create_index("ix_motivation_accruals_discipline", "motivation_accruals", ["discipline"])
    op.create_index("ix_motivation_accruals_occurrence_key", "motivation_accruals", ["occurrence_key"], unique=True)
    op.create_index("ix_motivation_accruals_occurrence_date", "motivation_accruals", ["occurrence_date"])


def downgrade():
    op.drop_table("motivation_accruals")
