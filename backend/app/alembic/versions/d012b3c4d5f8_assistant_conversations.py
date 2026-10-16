"""in-app assistant conversations (WP14a, E20)

assistantconversation: one chat thread per user and workspace (project_id is
only the focus it was opened from).
assistantmessage: questions (stored redacted) and answers with the refs they
cite and the read-only tool calls behind them. Kept 30 days.

Revision ID: d012b3c4d5f8
Revises: cf32b3c4d5f7
Create Date: 2026-10-16 23:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'd012b3c4d5f8'
down_revision: Union[str, None] = 'cf32b3c4d5f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'assistantconversation',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id']),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_assistantconversation_user_id'), 'assistantconversation', ['user_id'], unique=False)
    op.create_index(op.f('ix_assistantconversation_workspace_id'), 'assistantconversation', ['workspace_id'], unique=False)
    op.create_index(op.f('ix_assistantconversation_project_id'), 'assistantconversation', ['project_id'], unique=False)
    op.create_index(op.f('ix_assistantconversation_updated_at'), 'assistantconversation', ['updated_at'], unique=False)
    op.create_table(
        'assistantmessage',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('role', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('citations', sa.JSON(), nullable=True),
        sa.Column('tool_calls', sa.JSON(), nullable=True),
        sa.Column('project_ids', sa.JSON(), nullable=True),
        sa.Column('uncited', sa.Boolean(), nullable=False),
        sa.Column('dropped_citations', sa.Integer(), nullable=False),
        sa.Column('provider', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['conversation_id'], ['assistantconversation.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_assistantmessage_conversation_id'), 'assistantmessage', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_assistantmessage_user_id'), 'assistantmessage', ['user_id'], unique=False)
    op.create_index(op.f('ix_assistantmessage_workspace_id'), 'assistantmessage', ['workspace_id'], unique=False)
    op.create_index(op.f('ix_assistantmessage_created_at'), 'assistantmessage', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_assistantmessage_created_at'), table_name='assistantmessage')
    op.drop_index(op.f('ix_assistantmessage_workspace_id'), table_name='assistantmessage')
    op.drop_index(op.f('ix_assistantmessage_user_id'), table_name='assistantmessage')
    op.drop_index(op.f('ix_assistantmessage_conversation_id'), table_name='assistantmessage')
    op.drop_table('assistantmessage')
    op.drop_index(op.f('ix_assistantconversation_updated_at'), table_name='assistantconversation')
    op.drop_index(op.f('ix_assistantconversation_project_id'), table_name='assistantconversation')
    op.drop_index(op.f('ix_assistantconversation_workspace_id'), table_name='assistantconversation')
    op.drop_index(op.f('ix_assistantconversation_user_id'), table_name='assistantconversation')
    op.drop_table('assistantconversation')
