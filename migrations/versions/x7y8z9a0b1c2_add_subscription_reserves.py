"""add paid subscription reserve lifecycle fields"""
from alembic import op
import sqlalchemy as sa

revision = "x7y8z9a0b1c2"
down_revision = "w5x6y7z8a9b0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("payment_orders", sa.Column("activation_status", sa.String(20), nullable=False, server_default="ACTIVE"))
    op.add_column("payment_orders", sa.Column("scheduled_start", sa.DateTime(), nullable=True))
    op.add_column("payment_orders", sa.Column("parent_order_id", sa.String(50), nullable=True))
    op.create_index("ix_payment_orders_activation_status", "payment_orders", ["activation_status"])
    op.create_index("ix_payment_orders_scheduled_start", "payment_orders", ["scheduled_start"])
    op.create_index("ix_payment_orders_parent_order_id", "payment_orders", ["parent_order_id"])


def downgrade():
    op.drop_index("ix_payment_orders_parent_order_id", table_name="payment_orders")
    op.drop_index("ix_payment_orders_scheduled_start", table_name="payment_orders")
    op.drop_index("ix_payment_orders_activation_status", table_name="payment_orders")
    op.drop_column("payment_orders", "parent_order_id")
    op.drop_column("payment_orders", "scheduled_start")
    op.drop_column("payment_orders", "activation_status")
