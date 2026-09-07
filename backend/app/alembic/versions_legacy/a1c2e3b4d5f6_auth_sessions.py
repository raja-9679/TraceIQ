"""auth_sessions

Revision ID: a1c2e3b4d5f6
Revises: f8b3c4d5e6f7
Create Date: 2026-07-13 12:00:00.000000

Reusable auth sessions (Playwright storageState):
  • testcase.is_auth_setup    — this case's successful run captures the state
  • testcase.use_auth_session — opt-out flag for login-flow tests
  • authsession table         — one storage_state blob per project
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c2e3b4d5f6'
down_revision: Union[str, None] = 'f8b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('testcase', sa.Column('is_auth_setup', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('testcase', sa.Column('use_auth_session', sa.Boolean(), nullable=False, server_default=sa.true()))

    op.create_table(
        'authsession',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('project.id'), nullable=False, unique=True),
        sa.Column('storage_state', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('captured_by_case_id', sa.Integer(), sa.ForeignKey('testcase.id'), nullable=True),
        sa.Column('captured_at', sa.DateTime(), nullable=False),
        sa.Column('max_age_minutes', sa.Integer(), nullable=False, server_default='720'),
    )
    op.create_index('ix_authsession_project_id', 'authsession', ['project_id'])


def downgrade() -> None:
    op.drop_index('ix_authsession_project_id', table_name='authsession')
    op.drop_table('authsession')
    op.drop_column('testcase', 'use_auth_session')
    op.drop_column('testcase', 'is_auth_setup')
