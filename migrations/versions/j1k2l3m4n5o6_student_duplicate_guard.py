"""student duplicate guard

Revision ID: j1k2l3m4n5o6
Revises: i3j4k5l6m7n8
Create Date: 2026-07-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "j1k2l3m4n5o6"
down_revision: Union[str, Sequence[str], None] = "i3j4k5l6m7n8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_students_club_name_bday_disc_phone",
        "students",
        ["club_id", "name", "birthday", "discipline", "parent_phone"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_students_club_name_bday_disc_phone", "students", type_="unique")
