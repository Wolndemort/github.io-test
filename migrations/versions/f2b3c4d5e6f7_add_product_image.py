"""add product image"""
from alembic import op
import sqlalchemy as sa
revision = "f2b3c4d5e6f7"
down_revision = "e1a2b3c4d5e6"
branch_labels = None
depends_on = None
def upgrade(): op.add_column("club_products", sa.Column("image_url", sa.String(500), nullable=True))
def downgrade(): op.drop_column("club_products", "image_url")
