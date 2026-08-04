"""add user password_hash

Nullable: OAuth accounts (Phase 2) never set a password.

Revision ID: 9c1f4e2a7d30
Revises: 5ab42b7585c8
Create Date: 2026-08-04

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = '9c1f4e2a7d30'
down_revision: str | None = '5ab42b7585c8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('users', sa.Column('password_hash', sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'password_hash')
