"""add analysis cost columns

`total_tokens_used` collapsed input and output into one number, which cannot be
priced — output tokens cost roughly 5x input. Splitting them, plus recording
the model that produced them, lets a finished run report what it actually cost
and keeps old rows correctly priced when the configured model changes.

Revision ID: c3d81f60a4b2
Revises: 9c1f4e2a7d30
Create Date: 2026-08-30

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = 'c3d81f60a4b2'
down_revision: str | None = '9c1f4e2a7d30'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('analyses', sa.Column('input_tokens', sa.Integer(), nullable=True))
    op.add_column('analyses', sa.Column('output_tokens', sa.Integer(), nullable=True))
    op.add_column('analyses', sa.Column('model', sa.String(length=64), nullable=True))
    op.add_column('analyses', sa.Column('cost_usd', sa.Numeric(precision=12, scale=6), nullable=True))


def downgrade() -> None:
    op.drop_column('analyses', 'cost_usd')
    op.drop_column('analyses', 'model')
    op.drop_column('analyses', 'output_tokens')
    op.drop_column('analyses', 'input_tokens')
