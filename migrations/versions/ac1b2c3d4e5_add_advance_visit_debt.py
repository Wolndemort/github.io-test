"""add controlled advance visit debt to students

Revision ID: ac1b2c3d4e5
Revises: ab1b2c3d4e5f
"""
from alembic import op
import sqlalchemy as sa


revision = "ac1b2c3d4e5"
down_revision = "ab1b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("students", sa.Column("advance_debt_days", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("students", sa.Column("advance_debt_lessons", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("students", "advance_debt_lessons")
    op.drop_column("students", "advance_debt_days")
