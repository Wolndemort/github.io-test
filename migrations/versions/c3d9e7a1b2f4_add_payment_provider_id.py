"""add provider payment id for billing idempotency"""
from alembic import op
import sqlalchemy as sa

revision = "c3d9e7a1b2f4"
down_revision = "7f725dd0b763"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payment_orders", sa.Column("provider_payment_id", sa.String(length=100), nullable=True))
    op.create_index("ix_payment_orders_provider_payment_id", "payment_orders", ["provider_payment_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_payment_orders_provider_payment_id", table_name="payment_orders")
    op.drop_column("payment_orders", "provider_payment_id")
