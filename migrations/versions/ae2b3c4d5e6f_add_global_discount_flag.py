"""add explicit club-wide discount mode"""
from alembic import op
import sqlalchemy as sa

revision = "ae2b3c4d5e6f"
down_revision = "ad1b2c3d4e5f"
branch_labels = None
depends_on = None

def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("discounts")}
    if "is_global" not in columns:
        op.add_column("discounts", sa.Column("is_global", sa.Boolean(), nullable=False, server_default="0"))
        op.create_index("ix_discounts_is_global", "discounts", ["is_global"])

def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "is_global" in {column["name"] for column in inspector.get_columns("discounts")}:
        op.drop_index("ix_discounts_is_global", table_name="discounts")
        op.drop_column("discounts", "is_global")
