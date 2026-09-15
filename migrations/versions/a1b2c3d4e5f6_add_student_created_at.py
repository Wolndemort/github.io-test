"""track student registration date for analytics"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "z8a9b0c1d2e3"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("students", sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")))
    op.create_index("ix_students_created_at", "students", ["created_at"])

def downgrade():
    op.drop_index("ix_students_created_at", table_name="students")
    op.drop_column("students", "created_at")
