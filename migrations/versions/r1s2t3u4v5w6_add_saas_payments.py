"""Add isolated SaaS platform payment orders and club auto-renew fields."""
from alembic import op
import sqlalchemy as sa

revision = "r1s2t3u4v5w6"
down_revision = "q8r9s0t1u2v3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("clubs", sa.Column("saas_rebill_id", sa.String(100), nullable=True))
    op.add_column("clubs", sa.Column("saas_auto_renew", sa.Boolean(), server_default="0", nullable=False))
    op.create_table(
        "saas_payment_orders",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("club_id", sa.Integer(), sa.ForeignKey("clubs.id"), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("amount_kopecks", sa.Integer(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), server_default="NEW", nullable=False),
        sa.Column("provider_payment_id", sa.String(100), nullable=True),
        sa.Column("payment_method_id", sa.String(100), nullable=True),
        sa.Column("auto_renew", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("provider_payment_id"),
    )
    op.create_index("ix_saas_payment_orders_club_id", "saas_payment_orders", ["club_id"])
    op.create_index("ix_saas_payment_orders_owner_id", "saas_payment_orders", ["owner_id"])
    op.create_index("ix_saas_payment_orders_status", "saas_payment_orders", ["status"])


def downgrade():
    op.drop_index("ix_saas_payment_orders_status", table_name="saas_payment_orders")
    op.drop_index("ix_saas_payment_orders_owner_id", table_name="saas_payment_orders")
    op.drop_index("ix_saas_payment_orders_club_id", table_name="saas_payment_orders")
    op.drop_table("saas_payment_orders")
    op.drop_column("clubs", "saas_auto_renew")
    op.drop_column("clubs", "saas_rebill_id")
