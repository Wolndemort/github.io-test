"""add provider payment id for billing idempotency"""
from alembic import op
import sqlalchemy as sa

revision = "c3d9e7a1b2f4"
down_revision = "7f725dd0b763"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # The original baseline migration omitted several tables that are part of
    # the current models. Keep this repair idempotent so existing production
    # databases are left untouched while a fresh database can be upgraded.
    if "subscriptions" not in inspector.get_table_names():
        op.create_table(
            "subscriptions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True),
            sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id", ondelete="SET NULL"), nullable=True),
            sa.Column("club_id", sa.Integer(), sa.ForeignKey("clubs.id"), nullable=False),
            sa.Column("rebill_id", sa.String(50), nullable=True),
            sa.Column("amount_kopecks", sa.Integer(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("next_charge_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])
        op.create_index("ix_subscriptions_student_id", "subscriptions", ["student_id"])
        op.create_index("ix_subscriptions_club_id", "subscriptions", ["club_id"])
        op.create_index("ix_subscriptions_next_charge_at", "subscriptions", ["next_charge_at"])

    if "payment_orders" not in inspector.get_table_names():
        op.create_table(
            "payment_orders",
            sa.Column("id", sa.String(50), primary_key=True),
            sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True),
            sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id", ondelete="SET NULL"), nullable=True),
            sa.Column("club_id", sa.Integer(), sa.ForeignKey("clubs.id"), nullable=False),
            sa.Column("discipline", sa.String(50), nullable=True),
            sa.Column("amount_kopecks", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="NEW"),
            sa.Column("type", sa.String(20), nullable=False),
            sa.Column("provider_payment_id", sa.String(100), nullable=True),
            sa.Column("lesson_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("days_to_add", sa.Integer(), nullable=False, server_default="30"),
            sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")),
        )
        op.create_index("ix_payment_orders_user_id", "payment_orders", ["user_id"])
        op.create_index("ix_payment_orders_student_id", "payment_orders", ["student_id"])
        op.create_index("ix_payment_orders_club_id", "payment_orders", ["club_id"])
        op.create_index("ix_payment_orders_provider_payment_id", "payment_orders", ["provider_payment_id"], unique=True)
    elif "provider_payment_id" not in {c["name"] for c in inspector.get_columns("payment_orders")}:
        op.add_column("payment_orders", sa.Column("provider_payment_id", sa.String(length=100), nullable=True))
        op.create_index("ix_payment_orders_provider_payment_id", "payment_orders", ["provider_payment_id"], unique=True)

    if "visit_logs" not in inspector.get_table_names():
        op.create_table(
            "visit_logs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
            sa.Column("club_id", sa.Integer(), sa.ForeignKey("clubs.id"), nullable=False),
            sa.Column("visited_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")),
            sa.Column("source", sa.String(32), nullable=True),
        )
        op.create_index("ix_visit_logs_student_id", "visit_logs", ["student_id"])
        op.create_index("ix_visit_logs_club_id", "visit_logs", ["club_id"])


def downgrade() -> None:
    # Keep downgrade conservative for databases that already contained these
    # tables before this compatibility repair.
    inspector = sa.inspect(op.get_bind())
    if "payment_orders" in inspector.get_table_names() and "provider_payment_id" in {c["name"] for c in inspector.get_columns("payment_orders")}:
        op.drop_index("ix_payment_orders_provider_payment_id", table_name="payment_orders")
        op.drop_column("payment_orders", "provider_payment_id")
