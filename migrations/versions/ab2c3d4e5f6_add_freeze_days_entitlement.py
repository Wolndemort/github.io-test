"""store freeze duration snapshot on subscription purchase"""
from alembic import op
import sqlalchemy as sa
revision = "ab2c3d4e5f6"
down_revision = "y7z8a9b0c1d2"
branch_labels = None
depends_on = None
def upgrade():
    op.add_column("students", sa.Column("freeze_days_entitlement", sa.Integer(), nullable=False, server_default="7"))
    op.execute("UPDATE students SET freeze_days_entitlement = 7 WHERE freeze_days_entitlement IS NULL")
def downgrade():
    op.drop_column("students", "freeze_days_entitlement")
