"""Store legal acceptance per user and club."""
from alembic import op
import sqlalchemy as sa

revision = "s3t4u5v6w7x8"
down_revision = "r1s2t3u4v5w6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "legal_acceptances",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("club_id", sa.Integer(), sa.ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("document_version", sa.String(32), server_default="1.0", nullable=False),
        sa.UniqueConstraint("user_id", "club_id", name="uq_legal_acceptance_user_club"),
    )
    op.create_index("ix_legal_acceptances_user_id", "legal_acceptances", ["user_id"])
    op.create_index("ix_legal_acceptances_club_id", "legal_acceptances", ["club_id"])


def downgrade():
    op.drop_index("ix_legal_acceptances_club_id", table_name="legal_acceptances")
    op.drop_index("ix_legal_acceptances_user_id", table_name="legal_acceptances")
    op.drop_table("legal_acceptances")
