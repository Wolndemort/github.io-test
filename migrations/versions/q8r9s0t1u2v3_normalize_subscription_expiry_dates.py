"""make subscription end dates inclusive through the end of the day"""
from alembic import op


revision = "q8r9s0t1u2v3"
down_revision = "p4q5r6s7t8u9"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        UPDATE students
        SET expire_date = date_trunc('day', expire_date) + interval '23 hours 59 minutes 59 seconds'
        WHERE expire_date IS NOT NULL
        """
    )


def downgrade():
    # The original time-of-day was not preserved; the calendar date remains unchanged.
    pass
