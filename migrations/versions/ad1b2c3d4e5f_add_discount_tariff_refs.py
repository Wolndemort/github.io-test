"""snapshot eligible tariffs on subscription discounts"""
from alembic import op
import sqlalchemy as sa

revision = "ad1b2c3d4e5f"
down_revision = "ac1b2c3d4e5"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("discounts", sa.Column("tariff_refs", sa.JSON(), nullable=False, server_default="[]"))

def downgrade():
    op.drop_column("discounts", "tariff_refs")
