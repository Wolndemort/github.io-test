"""add discount rules and profile assignments"""
from alembic import op
import sqlalchemy as sa

revision = "t2u3v4w5x6y7"
down_revision = "r1s2t3u4v5w6"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("discounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("club_id", sa.Integer(), sa.ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False, server_default="subscriptions"),
        sa.Column("comment", sa.String(500)),
        sa.Column("starts_at", sa.Date()), sa.Column("ends_at", sa.Date()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default="now()"))
    op.create_table("discount_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("club_id", sa.Integer(), sa.ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("discount_id", sa.Integer(), sa.ForeignKey("discounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default="now()"),
        sa.UniqueConstraint("club_id", "discount_id", "user_id", name="uq_discount_assignment"))
    op.create_index("ix_discounts_club_id", "discounts", ["club_id"])
    op.create_index("ix_discount_assignments_club_id", "discount_assignments", ["club_id"])
    op.create_index("ix_discount_assignments_discount_id", "discount_assignments", ["discount_id"])
    op.create_index("ix_discount_assignments_user_id", "discount_assignments", ["user_id"])

def downgrade():
    op.drop_table("discount_assignments")
    op.drop_table("discounts")
