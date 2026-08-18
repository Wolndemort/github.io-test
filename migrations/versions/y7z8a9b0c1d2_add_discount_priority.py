"""add discount stacking priority"""
from alembic import op
import sqlalchemy as sa
revision = "y7z8a9b0c1d2"
down_revision = "x6y7z8a9b0c1"
branch_labels = None
depends_on = None
def upgrade():
    op.add_column("discounts", sa.Column("priority", sa.Integer(), nullable=False, server_default="100"))
def downgrade():
    op.drop_column("discounts", "priority")
