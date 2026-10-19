"""exam_categories (board grouping) + categories.exam_category_id

Revision ID: k5l6m7n8o9p0
Revises: j4k5l6m7n8o9
Create Date: 2026-10-19
"""
from alembic import op


revision = "k5l6m7n8o9p0"
down_revision = "j4k5l6m7n8o9"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE TABLE IF NOT EXISTS exam_categories ("
        " id SERIAL PRIMARY KEY,"
        " name VARCHAR(100) NOT NULL UNIQUE,"
        " order_index INTEGER NOT NULL DEFAULT 0,"
        " is_active BOOLEAN DEFAULT true,"
        " created_at TIMESTAMPTZ DEFAULT now(),"
        " updated_at TIMESTAMPTZ)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_exam_categories_id ON exam_categories (id)")
    op.execute(
        "ALTER TABLE categories ADD COLUMN IF NOT EXISTS exam_category_id INTEGER "
        "REFERENCES exam_categories(id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_categories_exam_category_id ON categories (exam_category_id)"
    )
    op.execute(
        "INSERT INTO exam_categories (name, order_index, is_active) VALUES "
        "('Entrance Exams', 1, true), ('Competitive Exams', 2, true), ('Academic Exams', 3, true) "
        "ON CONFLICT (name) DO NOTHING"
    )


def downgrade():
    op.execute("ALTER TABLE categories DROP COLUMN IF EXISTS exam_category_id")
    op.execute("DROP TABLE IF EXISTS exam_categories")
