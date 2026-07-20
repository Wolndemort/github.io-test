"""store actual duration of an active freeze"""
from alembic import op
import sqlalchemy as sa

revision = "d4e8f9a0b1c2"
down_revision = "c3d9e7a1b2f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("students", sa.Column("frozen_days", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("students", "frozen_days")
