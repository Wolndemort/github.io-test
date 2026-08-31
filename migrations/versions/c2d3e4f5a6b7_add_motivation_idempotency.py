"""add idempotency key to motivation adjustments

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
"""
from alembic import op
import sqlalchemy as sa

revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("motivation_adjustments", sa.Column("idempotency_key", sa.String(length=80), nullable=True))
    op.create_index("ix_motivation_adjustments_idempotency_key", "motivation_adjustments", ["idempotency_key"], unique=True)

def downgrade():
    op.drop_index("ix_motivation_adjustments_idempotency_key", table_name="motivation_adjustments")
    op.drop_column("motivation_adjustments", "idempotency_key")
