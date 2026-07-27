"""merge student duplicate guard head

Revision ID: k1l2m3n4o5p6
Revises: 4c9d2f1a8b6e, j1k2l3m4n5o6
Create Date: 2026-07-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "k1l2m3n4o5p6"
down_revision: Union[str, Sequence[str], None] = ("4c9d2f1a8b6e", "j1k2l3m4n5o6")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
