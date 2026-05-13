"""dual_role_composite_unique_constraints

Allows the same phone number / Firebase UID to exist as both a STUDENT and a
TEACHER.  Previously phone_number and firebase_uid each had a single-column
UNIQUE index, making it impossible for one person to use both apps.

Changes:
  - Drop UNIQUE INDEX ix_users_phone_number   → replaced by UNIQUE INDEX uq_users_phone_role
  - Drop UNIQUE INDEX ix_users_firebase_uid   → replaced by UNIQUE INDEX uq_users_firebase_role

Revision ID: h2i3j4k5l6m7
Revises:     g1h2i3j4k5l6
"""

from alembic import op

revision     = 'h2i3j4k5l6m7'
down_revision = 'g1h2i3j4k5l6'
branch_labels = None
depends_on    = None


def upgrade():
    # ── Drop old single-column unique indexes ─────────────────────────────────
    # These were created by SQLAlchemy's index=True + unique=True on columns.
    op.drop_index('ix_users_phone_number', table_name='users')
    op.drop_index('ix_users_firebase_uid', table_name='users')

    # ── Add composite unique indexes ──────────────────────────────────────────
    # Same phone/Firebase UID CAN appear twice if roles differ.
    # NULLs in PostgreSQL are never considered equal in unique indexes,
    # so admin rows (both columns NULL) are unaffected.
    op.create_index(
        'uq_users_phone_role',
        'users',
        ['phone_number', 'role'],
        unique=True,
    )
    op.create_index(
        'uq_users_firebase_role',
        'users',
        ['firebase_uid', 'role'],
        unique=True,
    )

    # Keep a plain (non-unique) index on each column for fast lookups
    op.create_index('ix_users_phone_number', 'users', ['phone_number'], unique=False)
    op.create_index('ix_users_firebase_uid', 'users', ['firebase_uid'], unique=False)


def downgrade():
    op.drop_index('uq_users_phone_role',    table_name='users')
    op.drop_index('uq_users_firebase_role', table_name='users')
    op.drop_index('ix_users_phone_number',  table_name='users')
    op.drop_index('ix_users_firebase_uid',  table_name='users')

    # Restore original single-column unique indexes
    op.create_index('ix_users_phone_number', 'users', ['phone_number'], unique=True)
    op.create_index('ix_users_firebase_uid', 'users', ['firebase_uid'], unique=True)
