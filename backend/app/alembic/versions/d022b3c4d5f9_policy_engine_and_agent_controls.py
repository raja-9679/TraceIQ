"""policy engine deny log + agent controls + AI opt-out (WP14b)

policydecisionlog: every DENY from app/services/policy (decision W4).
agentcontrol / agentsession: per-agent kill switch and per-session budgets
(decision W3). workspace.ai_enabled: the workspace AI opt-out (decision W2).
testrun.agent_session_id and llmusageevent.agent_id/agent_session_id: what an
agent session did, for the Agents page and budget accounting.

Revision ID: d022b3c4d5f9
Revises: d012b3c4d5f8
Create Date: 2026-10-16 23:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'd022b3c4d5f9'
down_revision: Union[str, None] = 'd012b3c4d5f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('workspace', sa.Column('ai_enabled', sa.Boolean(), nullable=False,
                                         server_default=sa.true()))
    op.add_column('testrun', sa.Column('agent_session_id',
                                       sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.create_index(op.f('ix_testrun_agent_session_id'), 'testrun', ['agent_session_id'], unique=False)
    op.add_column('llmusageevent', sa.Column('agent_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('llmusageevent', sa.Column('agent_session_id',
                                             sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.create_index(op.f('ix_llmusageevent_agent_session_id'), 'llmusageevent',
                    ['agent_session_id'], unique=False)

    op.create_table(
        'policydecisionlog',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('action', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('rule', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('reason', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=True),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('api_key_id', sa.Integer(), nullable=True),
        sa.Column('agent_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('agent_session_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('data', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    for col in ('action', 'rule', 'workspace_id', 'project_id', 'agent_id', 'created_at'):
        op.create_index(op.f(f'ix_policydecisionlog_{col}'), 'policydecisionlog', [col], unique=False)

    op.create_table(
        'agentcontrol',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('agent_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('blocked', sa.Boolean(), nullable=False),
        sa.Column('blocked_reason', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('max_runs', sa.Integer(), nullable=True),
        sa.Column('max_llm_tokens', sa.Integer(), nullable=True),
        sa.Column('max_proposals', sa.Integer(), nullable=True),
        sa.Column('max_minutes', sa.Integer(), nullable=True),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['updated_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('workspace_id', 'agent_id', name='uq_agentcontrol_ws_agent'),
    )
    op.create_index(op.f('ix_agentcontrol_workspace_id'), 'agentcontrol', ['workspace_id'], unique=False)

    op.create_table(
        'agentsession',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('agent_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('agent_session_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('runs', sa.Integer(), nullable=False),
        sa.Column('proposals', sa.Integer(), nullable=False),
        sa.Column('llm_tokens', sa.Integer(), nullable=False),
        sa.Column('denials', sa.Integer(), nullable=False),
        sa.Column('first_seen_at', sa.DateTime(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('workspace_id', 'agent_id', 'agent_session_id',
                            name='uq_agentsession_ws_agent_session'),
    )
    for col in ('workspace_id', 'agent_id', 'agent_session_id'):
        op.create_index(op.f(f'ix_agentsession_{col}'), 'agentsession', [col], unique=False)


def downgrade() -> None:
    for col in ('workspace_id', 'agent_id', 'agent_session_id'):
        op.drop_index(op.f(f'ix_agentsession_{col}'), table_name='agentsession')
    op.drop_table('agentsession')
    op.drop_index(op.f('ix_agentcontrol_workspace_id'), table_name='agentcontrol')
    op.drop_table('agentcontrol')
    for col in ('action', 'rule', 'workspace_id', 'project_id', 'agent_id', 'created_at'):
        op.drop_index(op.f(f'ix_policydecisionlog_{col}'), table_name='policydecisionlog')
    op.drop_table('policydecisionlog')
    op.drop_index(op.f('ix_llmusageevent_agent_session_id'), table_name='llmusageevent')
    op.drop_column('llmusageevent', 'agent_session_id')
    op.drop_column('llmusageevent', 'agent_id')
    op.drop_index(op.f('ix_testrun_agent_session_id'), table_name='testrun')
    op.drop_column('testrun', 'agent_session_id')
    op.drop_column('workspace', 'ai_enabled')
