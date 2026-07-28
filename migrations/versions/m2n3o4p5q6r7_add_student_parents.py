"""add multiple parents per student"""
from alembic import op
import sqlalchemy as sa

revision = "m2n3o4p5q6r7"
down_revision = "k1l2m3n4o5p6"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("student_parents",
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("student_id", "parent_id"))
    op.execute("INSERT INTO student_parents (student_id, parent_id, is_primary) SELECT id, parent_id, TRUE FROM students WHERE parent_id IS NOT NULL")

def downgrade():
    op.drop_table("student_parents")
