"""add per-discipline rates and immutable motivation accruals

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5
"""
from alembic import op
import sqlalchemy as sa

revision = "b1c2d3e4f5a6"
down_revision = "a0b1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("motivation_rates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("club_id", sa.Integer(), sa.ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("staff_id", sa.Integer(), sa.ForeignKey("club_staff.id", ondelete="CASCADE"), nullable=False),
        sa.Column("discipline", sa.String(length=50), nullable=False),
        sa.Column("rate_kopecks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")))
    op.create_index("ix_motivation_rates_club_id", "motivation_rates", ["club_id"])
    op.create_index("ix_motivation_rates_staff_id", "motivation_rates", ["staff_id"])
    op.create_unique_constraint("uq_motivation_rate_staff_discipline", "motivation_rates", ["club_id", "staff_id", "discipline"])
    op.create_table("motivation_accruals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("club_id", sa.Integer(), sa.ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("staff_id", sa.Integer(), sa.ForeignKey("club_staff.id", ondelete="CASCADE"), nullable=False),
        sa.Column("occurrence_key", sa.String(length=180), nullable=False, unique=True),
        sa.Column("occurrence_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.String(length=5), nullable=False),
        sa.Column("discipline", sa.String(length=50), nullable=False),
        sa.Column("rate_kopecks", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")))
    for name, column in (("club_id", "club_id"), ("staff_id", "staff_id"), ("occurrence_date", "occurrence_date")):
        op.create_index(f"ix_motivation_accruals_{name}", "motivation_accruals", [column])


def downgrade():
    op.drop_table("motivation_accruals")
    op.drop_constraint("uq_motivation_rate_staff_discipline", "motivation_rates", type_="unique")
    op.drop_table("motivation_rates")
