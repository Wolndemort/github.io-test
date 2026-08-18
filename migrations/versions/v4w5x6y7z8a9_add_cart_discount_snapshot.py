"""store cart discount snapshot"""
from alembic import op
import sqlalchemy as sa
revision = "v4w5x6y7z8a9"
down_revision = "u3v4w5x6y7z8"
branch_labels = None
depends_on = None
def upgrade():
    for name in ("original_amount_kopecks", "discount_id", "discount_name", "discount_amount_kopecks"):
        typ = sa.String(120) if name == "discount_name" else sa.Integer()
        op.add_column("cart_orders", sa.Column(name, typ, nullable=True))
def downgrade():
    for name in ("discount_amount_kopecks", "discount_name", "discount_id", "original_amount_kopecks"):
        op.drop_column("cart_orders", name)
