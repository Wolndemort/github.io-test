"""add email to students

Revision ID: aa1b2c3d4e5f
Revises: t4u5v6w7x8y9
"""
from alembic import op
import sqlalchemy as sa


revision = "aa1b2c3d4e5f"
down_revision = "t4u5v6w7x8y9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("students", sa.Column("email", sa.String(length=320), nullable=True))


def downgrade() -> None:
    op.drop_column("students", "email")
