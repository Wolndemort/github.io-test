"""add email to students"""
from alembic import op
import sqlalchemy as sa

revision = "aa1b2c3d4e5f"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("students", sa.Column("email", sa.String(length=320), nullable=True))

def downgrade() -> None:
    op.drop_column("students", "email")
