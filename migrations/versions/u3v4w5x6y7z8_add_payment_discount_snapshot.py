"""store discount snapshots on payment orders"""
from alembic import op
import sqlalchemy as sa

revision = "u3v4w5x6y7z8"
down_revision = "t2u3v4w5x6y7"
branch_labels = None
depends_on = None

def upgrade():
    for name, typ in (("original_amount_kopecks", sa.Integer()), ("discount_id", sa.Integer()), ("discount_name", sa.String(120)), ("discount_kind", sa.String(10)), ("discount_value", sa.Integer()), ("discount_amount_kopecks", sa.Integer())):
        op.add_column("payment_orders", sa.Column(name, typ, nullable=True))

def downgrade():
    for name in ("discount_amount_kopecks", "discount_value", "discount_kind", "discount_name", "discount_id", "original_amount_kopecks"):
        op.drop_column("payment_orders", name)
