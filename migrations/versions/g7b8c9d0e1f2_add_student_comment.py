"""Add internal comments to athlete cards."""

from alembic import op
import sqlalchemy as sa


revision = "g7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("students", sa.Column("comment", sa.String(length=1000), nullable=True))


def downgrade():
    op.drop_column("students", "comment")
