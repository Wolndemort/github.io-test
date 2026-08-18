"""allow discounts for students without a profile"""
from alembic import op
import sqlalchemy as sa
revision = "x6y7z8a9b0c1"
down_revision = "w5x6y7z8a9b0"
branch_labels = None
depends_on = None
def upgrade():
    op.alter_column("discount_assignments", "user_id", nullable=True)
    op.add_column("discount_assignments", sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=True))
    op.create_index("ix_discount_assignments_student_id", "discount_assignments", ["student_id"])
def downgrade():
    op.drop_index("ix_discount_assignments_student_id", table_name="discount_assignments")
    op.drop_column("discount_assignments", "student_id")
    op.alter_column("discount_assignments", "user_id", nullable=False)
