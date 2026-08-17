"""Add idempotency keys for manual cash-register entries."""

from alembic import op
import sqlalchemy as sa


revision = "f6a7b8c9d0e1"
down_revision = "r1s2t3u4v5w6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("cash_entries", sa.Column("idempotency_key", sa.String(length=80), nullable=True))
    op.create_index("ix_cash_entries_idempotency_key", "cash_entries", ["idempotency_key"], unique=True)


def downgrade():
    op.drop_index("ix_cash_entries_idempotency_key", table_name="cash_entries")
    op.drop_column("cash_entries", "idempotency_key")
