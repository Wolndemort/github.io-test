"""remove the abandoned trainer motivation subsystem"""

from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DROP TABLE IF EXISTS motivation_individuals")
    op.execute("DROP TABLE IF EXISTS motivation_accruals")
    op.execute("DROP TABLE IF EXISTS motivation_adjustments")
    op.execute("DROP TABLE IF EXISTS motivation_rates")


def downgrade():
    # The abandoned subsystem is intentionally not recreated.
    pass
