"""add coach rates and motivation adjustments

Revision ID: a0b1c2d3e4f5
Revises: z9a0b1c2d3e4
"""
from alembic import op
import sqlalchemy as sa

revision = "a0b1c2d3e4f5"
down_revision = "z9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("club_staff", sa.Column("rate_per_training_kopecks", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "motivation_adjustments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("club_id", sa.Integer(), sa.ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("staff_id", sa.Integer(), sa.ForeignKey("club_staff.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount_kopecks", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_motivation_adjustments_club_id", "motivation_adjustments", ["club_id"])
    op.create_index("ix_motivation_adjustments_staff_id", "motivation_adjustments", ["staff_id"])
    op.create_index("ix_motivation_adjustments_created_at", "motivation_adjustments", ["created_at"])


def downgrade():
    op.drop_table("motivation_adjustments")
    op.drop_column("club_staff", "rate_per_training_kopecks")
