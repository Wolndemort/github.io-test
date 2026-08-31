"""add weekday and weekend motivation rates

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
"""
from alembic import op
import sqlalchemy as sa

revision = "e4f5a6b7c8d9"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("motivation_rates", sa.Column("weekday_rate_kopecks", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("motivation_rates", sa.Column("weekend_rate_kopecks", sa.Integer(), nullable=False, server_default="0"))

def downgrade():
    op.drop_column("motivation_rates", "weekend_rate_kopecks")
    op.drop_column("motivation_rates", "weekday_rate_kopecks")
