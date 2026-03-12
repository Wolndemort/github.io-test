"""Initial_setup

Revision ID: aa2666293a2f
Revises: 
Create Date: 2026-03-12 13:30:18.876518

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'aa2666293a2f'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('users',
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('full_name', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('user_id'))
    op.create_table('students',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('parent_id', sa.BigInteger, sa.ForeignKey('users.user_id')),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('expire_date', sa.DateTime(), nullable=True),
        sa.Column('can_freeze', sa.Integer(), server_default='1'),
        sa.Column('is_frozen', sa.Integer(), server_default='0'),
        sa.Column('balance_lessons', sa.Integer(), server_default='0'),
        sa.Column('last_visit', sa.DateTime(), nullable=True),
        sa.Column('parent_phone', sa.String(length=20), nullable=True),
        sa.PrimaryKeyConstraint('id')
)


def downgrade() -> None:
    """Downgrade schema."""
    pass
