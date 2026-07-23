"""merge the legacy repair and catalog migration heads

This is a metadata-only merge. It changes no tables and makes future Alembic
revisions unambiguous after both existing branches have been applied.
"""
from typing import Sequence, Union


revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = ("abf9d31d7a46", "f2b3c4d5e6f7")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
