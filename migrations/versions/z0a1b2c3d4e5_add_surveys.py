"""add surveys and survey answers
Revision ID: z0a1b2c3d4e5
Revises: t4u5v6w7x8y9
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "z0a1b2c3d4e5"
down_revision = "t4u5v6w7x8y9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "surveys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("club_id", sa.Integer(), sa.ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("intro_text", sa.String(2000), nullable=False),
        sa.Column("trigger_days", sa.Integer(), nullable=True),
        sa.Column("filters", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_surveys_club_id", "surveys", ["club_id"])
    op.create_index("ix_surveys_trigger_days", "surveys", ["trigger_days"])
    op.create_index("ix_surveys_is_active", "surveys", ["is_active"])
    op.create_table(
        "survey_questions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("survey_id", sa.Integer(), sa.ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("text", sa.String(1000), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="single"),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("required", sa.Boolean(), nullable=False, server_default="1"),
        sa.UniqueConstraint("survey_id", "position", name="uq_survey_question_position"),
    )
    op.create_index("ix_survey_questions_survey_id", "survey_questions", ["survey_id"])
    op.create_table(
        "survey_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("survey_id", sa.Integer(), sa.ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False),
        sa.Column("club_id", sa.Integer(), sa.ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recipient_id", sa.BigInteger(), nullable=False),
        sa.Column("trigger_key", sa.String(220), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("survey_id", "student_id", "recipient_id", "trigger_key", name="uq_survey_delivery_trigger"),
    )
    for name, col in (("survey_id", "survey_id"), ("club_id", "club_id"), ("student_id", "student_id"), ("recipient_id", "recipient_id"), ("status", "status")):
        op.create_index(f"ix_survey_deliveries_{name}", "survey_deliveries", [col])
    op.create_table(
        "survey_answers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("delivery_id", sa.Integer(), sa.ForeignKey("survey_deliveries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("survey_id", sa.Integer(), sa.ForeignKey("surveys.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_id", sa.Integer(), sa.ForeignKey("survey_questions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recipient_id", sa.BigInteger(), nullable=False),
        sa.Column("value", sa.String(4000), nullable=False),
        sa.Column("value_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("delivery_id", "question_id", name="uq_survey_answer_question"),
    )
    for name, col in (("delivery_id", "delivery_id"), ("survey_id", "survey_id"), ("question_id", "question_id"), ("student_id", "student_id"), ("recipient_id", "recipient_id")):
        op.create_index(f"ix_survey_answers_{name}", "survey_answers", [col])


def downgrade():
    op.drop_table("survey_answers")
    op.drop_table("survey_deliveries")
    op.drop_table("survey_questions")
    op.drop_table("surveys")
