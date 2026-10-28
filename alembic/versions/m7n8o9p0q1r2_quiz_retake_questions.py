"""quiz retakes: per-attempt AI questions (quiz_questions.attempt_id) + served ids

Revision ID: m7n8o9p0q1r2
Revises: l6m7n8o9p0q1
Create Date: 2026-10-28
"""
from alembic import op


revision = "m7n8o9p0q1r2"
down_revision = "l6m7n8o9p0q1"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE quiz_questions ADD COLUMN IF NOT EXISTS attempt_id INTEGER "
        "REFERENCES quiz_attempts(id) ON DELETE CASCADE"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_quiz_questions_attempt_id ON quiz_questions (attempt_id)"
    )
    op.execute("ALTER TABLE quiz_attempts ADD COLUMN IF NOT EXISTS question_ids JSON")


def downgrade():
    op.execute("DELETE FROM quiz_questions WHERE attempt_id IS NOT NULL")
    op.drop_index("ix_quiz_questions_attempt_id", table_name="quiz_questions")
    op.drop_column("quiz_questions", "attempt_id")
    op.drop_column("quiz_attempts", "question_ids")
