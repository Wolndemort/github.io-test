"""add isolated club staff accounts and permissions"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "i3j4k5l6m7n8"
down_revision = "h2i3j4k5l6m7"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "club_staff",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("club_id", sa.Integer(), sa.ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("full_name", sa.String(150), nullable=True),
        sa.Column("role", sa.String(32), nullable=False, server_default="cashier"),
        sa.Column("permissions", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_club_staff_club_id", "club_staff", ["club_id"])
    op.create_index("ix_club_staff_telegram_id", "club_staff", ["telegram_id"])
    op.create_unique_constraint("uq_club_staff_club_telegram", "club_staff", ["club_id", "telegram_id"])

def downgrade():
    op.drop_constraint("uq_club_staff_club_telegram", "club_staff", type_="unique")
    op.drop_index("ix_club_staff_telegram_id", table_name="club_staff")
    op.drop_index("ix_club_staff_club_id", table_name="club_staff")
    op.drop_table("club_staff")
