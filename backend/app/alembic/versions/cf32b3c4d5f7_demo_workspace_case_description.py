"""demo workspace flag + test case description (WP13c)

workspace.is_demo / demo_owner_id: a seeded demo workspace (decision C5) is an
ordinary workspace with an explicit flag, so the notification paths can refuse
to deliver for it and "one active demo per user" is a simple lookup.

testcase.description: integration test packs (C7) describe every case, and a
suite export/import round-trip has to carry that text somewhere.

Revision ID: cf32b3c4d5f7
Revises: cf22b3c4d5f6
Create Date: 2026-10-16 22:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'cf32b3c4d5f7'
down_revision: Union[str, None] = 'cf22b3c4d5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('workspace', sa.Column('is_demo', sa.Boolean(), nullable=False,
                                         server_default=sa.false()))
    op.add_column('workspace', sa.Column('demo_owner_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_workspace_demo_owner_id'), 'workspace', ['demo_owner_id'], unique=False)
    # Unnamed on purpose: matches the model's generated name, so `alembic
    # check` sees no drift.
    op.create_foreign_key(None, 'workspace', 'users', ['demo_owner_id'], ['id'])
    op.add_column('testcase', sa.Column('description', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('testcase', 'description')
    op.drop_constraint('workspace_demo_owner_id_fkey', 'workspace', type_='foreignkey')
    op.drop_index(op.f('ix_workspace_demo_owner_id'), table_name='workspace')
    op.drop_column('workspace', 'demo_owner_id')
    op.drop_column('workspace', 'is_demo')
