"""rename_is_active_to_expire_at

Revision ID: 7f725dd0b763
Revises: b715375a5ee5
Create Date: 2026-04-15 14:41:41.004453

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7f725dd0b763'
down_revision: Union[str, Sequence[str], None] = 'b715375a5ee5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
