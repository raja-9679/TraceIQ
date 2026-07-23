"""LLM usage metering: per-call token events

Revision ID: a5b6c7d8e9f0
Revises: c3d4e5f6a7b8, e4f5a6b7c8d9
Create Date: 2026-07-23 10:00:00.000000

One row per LLM API call (provider, model, feature, tokens, latency) feeding
the AI-usage dashboard. Monthly totals roll up into usagerecord
(metric="llm_tokens") for plan quota enforcement — add a
`monthly_llm_tokens` key to plan.limits to turn the cap on (absent/0 =
unlimited, so this migration changes no behavior by itself).

Also merges the two pre-existing heads (dataset_column, requirement_links)
back into a single lineage.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a5b6c7d8e9f0'
down_revision: Union[str, Sequence[str], None] = ('c3d4e5f6a7b8', 'e4f5a6b7c8d9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'llmusageevent',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=True),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('run_id', sa.Integer(), nullable=True),
        sa.Column('provider', sa.String(), nullable=False),
        sa.Column('model', sa.String(), nullable=False, server_default=''),
        sa.Column('feature', sa.String(), nullable=False, server_default='unknown'),
        sa.Column('source', sa.String(), nullable=False, server_default='backend'),
        sa.Column('input_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('output_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('latency_ms', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('success', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('error', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_llmusageevent_workspace_id', 'llmusageevent', ['workspace_id'])
    op.create_index('ix_llmusageevent_project_id', 'llmusageevent', ['project_id'])
    op.create_index('ix_llmusageevent_run_id', 'llmusageevent', ['run_id'])
    op.create_index('ix_llmusageevent_provider', 'llmusageevent', ['provider'])
    op.create_index('ix_llmusageevent_model', 'llmusageevent', ['model'])
    op.create_index('ix_llmusageevent_feature', 'llmusageevent', ['feature'])
    op.create_index('ix_llmusageevent_created_at', 'llmusageevent', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_llmusageevent_created_at', table_name='llmusageevent')
    op.drop_index('ix_llmusageevent_feature', table_name='llmusageevent')
    op.drop_index('ix_llmusageevent_model', table_name='llmusageevent')
    op.drop_index('ix_llmusageevent_provider', table_name='llmusageevent')
    op.drop_index('ix_llmusageevent_run_id', table_name='llmusageevent')
    op.drop_index('ix_llmusageevent_project_id', table_name='llmusageevent')
    op.drop_index('ix_llmusageevent_workspace_id', table_name='llmusageevent')
    op.drop_table('llmusageevent')
