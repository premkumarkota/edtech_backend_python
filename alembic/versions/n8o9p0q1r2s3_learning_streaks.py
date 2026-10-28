"""learning streaks: study_streaks freezes/repair columns + student_learning_days

Revision ID: n8o9p0q1r2s3
Revises: m7n8o9p0q1r2
Create Date: 2026-10-28
"""
from alembic import op


revision = "n8o9p0q1r2s3"
down_revision = "m7n8o9p0q1r2"
branch_labels = None
depends_on = None


def upgrade():
    for col, typ in (("freezes", "INTEGER NOT NULL DEFAULT 0"),
                     ("broken_streak", "INTEGER NOT NULL DEFAULT 0"),
                     ("broken_on", "DATE"),
                     ("repair_used_on", "DATE"),
                     ("last_reminder_on", "DATE")):
        op.execute(f"ALTER TABLE study_streaks ADD COLUMN IF NOT EXISTS {col} {typ}")
    op.execute("""
        CREATE TABLE IF NOT EXISTS student_learning_days (
            id SERIAL PRIMARY KEY,
            student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            day DATE NOT NULL,
            activities INTEGER NOT NULL DEFAULT 0,
            sources JSON,
            frozen BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ,
            CONSTRAINT uq_student_learning_day UNIQUE (student_id, day)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_student_learning_days_student_id ON student_learning_days (student_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_student_learning_days_day ON student_learning_days (day)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS student_learning_days")
    for col in ("last_reminder_on", "repair_used_on", "broken_on", "broken_streak", "freezes"):
        op.drop_column("study_streaks", col)
