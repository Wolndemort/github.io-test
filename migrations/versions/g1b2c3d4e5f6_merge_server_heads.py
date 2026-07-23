"""merge the server's legacy finance/visit branch with the catalog branch

Metadata-only merge. No tables or data are changed.
"""
from typing import Sequence, Union


revision: str = "g1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = ("cf57c88be37e", "f7a8b9c0d1e2")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
