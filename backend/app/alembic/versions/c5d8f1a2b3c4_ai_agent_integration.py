"""ai_agent_integration

Revision ID: c5d8f1a2b3c4
Revises: b2c4e6f8a0d1
Create Date: 2026-05-13 00:00:00.000000

Phase A schema for making TraceIQ AI-agent-callable:
- New tables: apikey, refreshtoken, workspacewebhook, visualbaseline
- New columns on testrun: git_commit, git_branch, git_pr_url, git_repo,
  triggered_by, agent_id, api_key_id
- New enum: runtrigger

Idempotent: uses IF NOT EXISTS for new objects so partial runs are safe.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5d8f1a2b3c4'
down_revision: Union[str, None] = 'b2c4e6f8a0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


RUN_TRIGGER_VALUES = ('human', 'schedule', 'api_agent', 'ci', 'webhook')


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # Enum: runtrigger
    # ------------------------------------------------------------------
    bind.execute(sa.text(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'runtrigger') THEN "
        "CREATE TYPE runtrigger AS ENUM ('human', 'schedule', 'api_agent', 'ci', 'webhook'); "
        "END IF; END $$;"
    ))

    # ------------------------------------------------------------------
    # testrun: change-awareness columns
    # ------------------------------------------------------------------
    op.add_column('testrun', sa.Column('git_commit', sa.String(), nullable=True))
    op.add_column('testrun', sa.Column('git_branch', sa.String(), nullable=True))
    op.add_column('testrun', sa.Column('git_pr_url', sa.String(), nullable=True))
    op.add_column('testrun', sa.Column('git_repo', sa.String(), nullable=True))
    op.add_column(
        'testrun',
        sa.Column(
            'triggered_by',
            sa.Enum(*RUN_TRIGGER_VALUES, name='runtrigger', create_type=False),
            server_default='human',
            nullable=False,
        ),
    )
    op.add_column('testrun', sa.Column('agent_id', sa.String(), nullable=True))
    op.add_column('testrun', sa.Column('api_key_id', sa.Integer(), nullable=True))

    op.create_index('ix_testrun_git_commit', 'testrun', ['git_commit'])
    op.create_index('ix_testrun_triggered_by', 'testrun', ['triggered_by'])
    op.create_index('ix_testrun_api_key_id', 'testrun', ['api_key_id'])

    # ------------------------------------------------------------------
    # apikey
    # ------------------------------------------------------------------
    op.create_table(
        'apikey',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('workspace_id', sa.Integer(), sa.ForeignKey('workspace.id'), nullable=False),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('project.id'), nullable=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('prefix', sa.String(), nullable=False),
        sa.Column('hashed_key', sa.String(), nullable=False),
        sa.Column('role_id', sa.Integer(), sa.ForeignKey('role.id'), nullable=True),
        sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_apikey_workspace_id', 'apikey', ['workspace_id'])
    op.create_index('ix_apikey_prefix', 'apikey', ['prefix'])

    # ------------------------------------------------------------------
    # refreshtoken
    # ------------------------------------------------------------------
    op.create_table(
        'refreshtoken',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('hashed_token', sa.String(), nullable=False),
        sa.Column('family_id', sa.String(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('user_agent', sa.String(), nullable=True),
        sa.Column('ip_address', sa.String(), nullable=True),
    )
    op.create_index('ix_refreshtoken_user_id', 'refreshtoken', ['user_id'])
    op.create_index('ix_refreshtoken_hashed_token', 'refreshtoken', ['hashed_token'])
    op.create_index('ix_refreshtoken_family_id', 'refreshtoken', ['family_id'])

    # ------------------------------------------------------------------
    # workspacewebhook
    # ------------------------------------------------------------------
    op.create_table(
        'workspacewebhook',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('workspace_id', sa.Integer(), sa.ForeignKey('workspace.id'), nullable=False),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('project.id'), nullable=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('url', sa.String(), nullable=False),
        sa.Column('secret', sa.String(), nullable=False),
        sa.Column('event_filter', sa.String(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('last_delivery_at', sa.DateTime(), nullable=True),
        sa.Column('last_delivery_status', sa.Integer(), nullable=True),
        sa.Column('failure_count', sa.Integer(), nullable=False, server_default='0'),
    )
    op.create_index('ix_workspacewebhook_workspace_id', 'workspacewebhook', ['workspace_id'])

    # ------------------------------------------------------------------
    # visualbaseline (Phase B scaffold)
    # ------------------------------------------------------------------
    op.create_table(
        'visualbaseline',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('test_case_id', sa.Integer(), sa.ForeignKey('testcase.id'), nullable=False),
        sa.Column('step_id', sa.String(), nullable=False),
        sa.Column('browser', sa.String(), nullable=False, server_default='chromium'),
        sa.Column('device', sa.String(), nullable=True),
        sa.Column('viewport', sa.String(), nullable=True),
        sa.Column('image_url', sa.String(), nullable=False),
        sa.Column('mask_regions', sa.JSON(), nullable=True),
        sa.Column('tolerance', sa.Float(), nullable=False, server_default='0.01'),
        sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_visualbaseline_test_case_id', 'visualbaseline', ['test_case_id'])
    op.create_index('ix_visualbaseline_step_id', 'visualbaseline', ['step_id'])


def downgrade() -> None:
    op.drop_index('ix_visualbaseline_step_id', table_name='visualbaseline')
    op.drop_index('ix_visualbaseline_test_case_id', table_name='visualbaseline')
    op.drop_table('visualbaseline')

    op.drop_index('ix_workspacewebhook_workspace_id', table_name='workspacewebhook')
    op.drop_table('workspacewebhook')

    op.drop_index('ix_refreshtoken_family_id', table_name='refreshtoken')
    op.drop_index('ix_refreshtoken_hashed_token', table_name='refreshtoken')
    op.drop_index('ix_refreshtoken_user_id', table_name='refreshtoken')
    op.drop_table('refreshtoken')

    op.drop_index('ix_apikey_prefix', table_name='apikey')
    op.drop_index('ix_apikey_workspace_id', table_name='apikey')
    op.drop_table('apikey')

    op.drop_index('ix_testrun_api_key_id', table_name='testrun')
    op.drop_index('ix_testrun_triggered_by', table_name='testrun')
    op.drop_index('ix_testrun_git_commit', table_name='testrun')
    op.drop_column('testrun', 'api_key_id')
    op.drop_column('testrun', 'agent_id')
    op.drop_column('testrun', 'triggered_by')
    op.drop_column('testrun', 'git_repo')
    op.drop_column('testrun', 'git_pr_url')
    op.drop_column('testrun', 'git_branch')
    op.drop_column('testrun', 'git_commit')

    op.get_bind().execute(sa.text('DROP TYPE IF EXISTS runtrigger'))
