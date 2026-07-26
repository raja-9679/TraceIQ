"""Local-worker run routing

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-07-23 16:00:00.000000

TestRun.local_worker_id pins a run's jobs to a developer's polling worker
(the localhost-testing bridge) instead of the server-side Redis stream.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e9f0a1b2c3d4'
down_revision: Union[str, None] = 'd8e9f0a1b2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('testrun', sa.Column('local_worker_id', sa.String(), nullable=True))
    op.create_index('ix_testrun_local_worker_id', 'testrun', ['local_worker_id'])


def downgrade() -> None:
    op.drop_index('ix_testrun_local_worker_id', table_name='testrun')
    op.drop_column('testrun', 'local_worker_id')
