"""initial schema with categories and verification

Revision ID: 002a8e56e6e0
Revises: 
Create Date: 2026-02-20 20:07:51.526359

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = '002a8e56e6e0'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    """Check if a table already exists in the database."""
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in inspector.get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column already exists in a table."""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    """Add new columns and categories table — safe for already-migrated DBs."""

    # 1. Create categories table ONLY if it doesn't exist
    if not _table_exists('categories'):
        op.create_table('categories',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=100), nullable=False),
            sa.Column('image_url', sa.String(length=500), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=True),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('name'),
        )
        op.create_index(op.f('ix_categories_id'), 'categories', ['id'], unique=False)

    # 2. Add new columns to users table ONLY if they don't exist

    # profile image (may already exist from an older migration)
    if not _column_exists('users', 'profile_image_url'):
        op.add_column('users', sa.Column('profile_image_url', sa.String(length=500), nullable=True))

    # Student fields
    if not _column_exists('users', 'dob'):
        op.add_column('users', sa.Column('dob', sa.String(length=50), nullable=True))
    if not _column_exists('users', 'age'):
        op.add_column('users', sa.Column('age', sa.Integer(), nullable=True))
    if not _column_exists('users', 'school_college'):
        op.add_column('users', sa.Column('school_college', sa.String(length=200), nullable=True))
    if not _column_exists('users', 'location'):
        op.add_column('users', sa.Column('location', sa.String(length=200), nullable=True))

    # Teacher fields
    if not _column_exists('users', 'document_url'):
        op.add_column('users', sa.Column('document_url', sa.String(length=500), nullable=True))
    if not _column_exists('users', 'is_verified'):
        op.add_column('users', sa.Column('is_verified', sa.Boolean(), nullable=True, server_default=sa.text('false')))

    # Shared fields
    if not _column_exists('users', 'category_id'):
        op.add_column('users', sa.Column('category_id', sa.Integer(), nullable=True))

        # Add FK only when column is freshly created
        op.create_foreign_key('fk_users_category_id', 'users', 'categories', ['category_id'], ['id'])

    # updated_at for users
    if not _column_exists('users', 'updated_at'):
        op.add_column('users', sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Remove new columns and categories table."""
    # Drop FK if it exists
    try:
        op.drop_constraint('fk_users_category_id', 'users', type_='foreignkey')
    except Exception:
        pass

    for col in ['category_id', 'is_verified', 'document_url', 'location',
                'school_college', 'age', 'dob', 'profile_image_url', 'updated_at']:
        if _column_exists('users', col):
            op.drop_column('users', col)

    if _table_exists('categories'):
        try:
            op.drop_index(op.f('ix_categories_id'), table_name='categories')
        except Exception:
            pass
        op.drop_table('categories')
