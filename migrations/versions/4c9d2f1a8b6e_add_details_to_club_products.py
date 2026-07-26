"""add details to club_products

Revision ID: 4c9d2f1a8b6e
Revises: i3j4k5l6m7n8
Create Date: 2026-07-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "4c9d2f1a8b6e"
down_revision: Union[str, Sequence[str], None] = "i3j4k5l6m7n8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "club_products" not in tables:
        return
    columns = {column["name"] for column in inspector.get_columns("club_products")}
    if "details" in columns:
        return
    op.add_column("club_products", sa.Column("details", sa.String(length=1000), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "club_products" not in tables:
        return
    columns = {column["name"] for column in inspector.get_columns("club_products")}
    if "details" not in columns:
        return
    op.drop_column("club_products", "details")
