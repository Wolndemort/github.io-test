"""add a second parent phone and preserve phone per parent link"""
from alembic import op
import sqlalchemy as sa


revision = "p4q5r6s7t8u9"
down_revision = "m2n3o4p5q6r7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("students", sa.Column("parent_phone_secondary", sa.String(length=20), nullable=True))
    op.add_column("student_parents", sa.Column("phone", sa.String(length=20), nullable=True))
    op.execute(
        "UPDATE student_parents sp SET phone = s.parent_phone "
        "FROM students s WHERE s.id = sp.student_id AND sp.is_primary = TRUE AND s.parent_phone IS NOT NULL"
    )


def downgrade():
    op.drop_column("student_parents", "phone")
    op.drop_column("students", "parent_phone_secondary")
