"""add attendance bonuses and individual motivation entries

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
"""
from alembic import op
import sqlalchemy as sa

revision = "d3e4f5a6b7c8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("motivation_rates", sa.Column("bonus_threshold", sa.Integer(), nullable=False, server_default="5"))
    op.add_column("motivation_rates", sa.Column("bonus_per_student_kopecks", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("motivation_accruals", sa.Column("student_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("motivation_accruals", sa.Column("bonus_kopecks", sa.Integer(), nullable=False, server_default="0"))
    op.create_table("motivation_individuals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("club_id", sa.Integer(), sa.ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("staff_id", sa.Integer(), sa.ForeignKey("club_staff.id", ondelete="CASCADE"), nullable=False),
        sa.Column("training_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.String(length=5), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("rate_kopecks", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False, unique=True),
        sa.Column("note", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")))
    op.create_index("ix_motivation_individuals_club_id", "motivation_individuals", ["club_id"])
    op.create_index("ix_motivation_individuals_staff_id", "motivation_individuals", ["staff_id"])
    op.create_index("ix_motivation_individuals_training_date", "motivation_individuals", ["training_date"])
    op.create_index("ix_motivation_individuals_idempotency_key", "motivation_individuals", ["idempotency_key"], unique=True)

def downgrade():
    op.drop_table("motivation_individuals")
    op.drop_column("motivation_accruals", "bonus_kopecks")
    op.drop_column("motivation_accruals", "student_count")
    op.drop_column("motivation_rates", "bonus_per_student_kopecks")
    op.drop_column("motivation_rates", "bonus_threshold")
