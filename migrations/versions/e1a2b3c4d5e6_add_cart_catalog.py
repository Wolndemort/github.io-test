"""add club catalog and cart foundation"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "e1a2b3c4d5e6"
down_revision = "d4e8f9a0b1c2"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("club_products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("club_id", sa.Integer(), sa.ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False), sa.Column("category", sa.String(30), nullable=False),
        sa.Column("price_kopecks", sa.Integer(), nullable=False), sa.Column("stock", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("created_at", sa.DateTime(), nullable=True))
    op.create_index("ix_club_products_club_id", "club_products", ["club_id"])
    op.create_table("cart_orders", sa.Column("id", sa.String(50), primary_key=True),
        sa.Column("club_id", sa.Integer(), sa.ForeignKey("clubs.id"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True),
        sa.Column("amount_kopecks", sa.Integer(), nullable=False), sa.Column("status", sa.String(20), nullable=False, server_default="NEW"),
        sa.Column("provider_payment_id", sa.String(100), nullable=True), sa.Column("created_at", sa.DateTime(), nullable=True))
    op.create_index("ix_cart_orders_provider_payment_id", "cart_orders", ["provider_payment_id"], unique=True)
    op.create_table("cart_items", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cart_order_id", sa.String(50), sa.ForeignKey("cart_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("club_products.id", ondelete="SET NULL"), nullable=True),
        sa.Column("item_type", sa.String(30), nullable=False), sa.Column("title", sa.String(160), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"), sa.Column("unit_price_kopecks", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"))
    op.create_index("ix_cart_items_cart_order_id", "cart_items", ["cart_order_id"])

def downgrade():
    op.drop_table("cart_items"); op.drop_index("ix_cart_orders_provider_payment_id", table_name="cart_orders"); op.drop_table("cart_orders"); op.drop_index("ix_club_products_club_id", table_name="club_products"); op.drop_table("club_products")
