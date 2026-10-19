"""use the club freeze step as the default entitlement"""

from alembic import op


revision = "e7f8a9b0c1d2"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "students",
        "freeze_days_entitlement",
        server_default="5",
    )


def downgrade():
    op.alter_column(
        "students",
        "freeze_days_entitlement",
        server_default="7",
    )
