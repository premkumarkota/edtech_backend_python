"""fix userrole enum to lowercase values

Revision ID: fix_userrole_enum_lc
Revises: dcd519e8a3e4
Create Date: 2026-03-06

Problem:
  PostgreSQL userrole enum has uppercase values: ADMIN, STUDENT, TEACHER
  SQLAlchemy model uses lowercase: admin, student, teacher
  This causes: invalid input value for enum userrole: "admin"

Fix:
  Rename old type -> create new lowercase type -> cast column -> drop old type.
  We do the migration in a single transaction using text casting to avoid
  the "unsafe new enum value" restriction PostgreSQL imposes on ADD VALUE.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'fix_userrole_enum_lc'
down_revision: Union[str, Sequence[str], None] = 'dcd519e8a3e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Step 1: Rename the old UPPERCASE enum type
    op.execute("ALTER TYPE userrole RENAME TO userrole_old")

    # Step 2: Create new enum with lowercase values
    op.execute("CREATE TYPE userrole AS ENUM ('admin', 'student', 'teacher')")

    # Step 3: Cast the column — first to text, then to the new enum
    #         This avoids the "unsafe new enum value" PostgreSQL restriction
    op.execute("""
        ALTER TABLE users
        ALTER COLUMN role TYPE userrole
        USING lower(role::text)::userrole
    """)

    # Step 4: Drop old enum
    op.execute("DROP TYPE userrole_old")


def downgrade() -> None:
    op.execute("ALTER TYPE userrole RENAME TO userrole_old")
    op.execute("CREATE TYPE userrole AS ENUM ('ADMIN', 'STUDENT', 'TEACHER')")
    op.execute("""
        ALTER TABLE users
        ALTER COLUMN role TYPE userrole
        USING upper(role::text)::userrole
    """)
    op.execute("DROP TYPE userrole_old")
