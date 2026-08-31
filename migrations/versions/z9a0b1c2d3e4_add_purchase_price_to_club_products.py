"""add purchase price to club products

Revision ID: z9a0b1c2d3e4
Revises: ab2c3d4e5f6
"""
from alembic import op
import sqlalchemy as sa

revision = "z9a0b1c2d3e4"
down_revision = "ab2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("club_products", sa.Column("purchase_price_kopecks", sa.Integer(), nullable=True))
    op.execute("UPDATE club_products SET purchase_price_kopecks = price_kopecks WHERE purchase_price_kopecks IS NULL")
    op.alter_column("club_products", "purchase_price_kopecks", nullable=False, server_default="0")


def downgrade():
    op.drop_column("club_products", "purchase_price_kopecks")
