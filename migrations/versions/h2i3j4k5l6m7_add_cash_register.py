"""add audited manual cash register entries"""
from alembic import op
import sqlalchemy as sa

revision = "h2i3j4k5l6m7"
down_revision = "g1b2c3d4e5f6"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "cash_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("club_id", sa.Integer(), sa.ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entry_type", sa.String(16), nullable=False),
        sa.Column("category", sa.String(50), nullable=False, server_default="other"),
        sa.Column("amount_kopecks", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(500), nullable=False, server_default=""),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("reversed_entry_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_cash_entries_club_id", "cash_entries", ["club_id"])
    op.create_index("ix_cash_entries_created_at", "cash_entries", ["created_at"])

def downgrade():
    op.drop_index("ix_cash_entries_created_at", table_name="cash_entries")
    op.drop_index("ix_cash_entries_club_id", table_name="cash_entries")
    op.drop_table("cash_entries")
