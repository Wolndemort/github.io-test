"""snapshot eligible tariffs on subscription discounts"""
from alembic import op
import sqlalchemy as sa

revision = "ad1b2c3d4e5f"
down_revision = "ac1b2c3d4e5"
branch_labels = None
depends_on = None

def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "tariff_refs" not in {column["name"] for column in inspector.get_columns("discounts")}:
        op.add_column("discounts", sa.Column("tariff_refs", sa.JSON(), nullable=False, server_default="[]"))

def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "tariff_refs" in {column["name"] for column in inspector.get_columns("discounts")}:
        op.drop_column("discounts", "tariff_refs")
