"""workspace max_concurrent_runs

Revision ID: b1d2e3f4a5b6
Revises: a9c1d2e3f4a5
Create Date: 2026-07-17 13:00:00.000000

Per-workspace concurrency cap enforced at dispatch (0 = unlimited).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1d2e3f4a5b6'
down_revision: Union[str, None] = 'a9c1d2e3f4a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'workspace',
        sa.Column('max_concurrent_runs', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('workspace', 'max_concurrent_runs')
