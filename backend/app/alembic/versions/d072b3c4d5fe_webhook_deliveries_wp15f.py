"""webhook delivery log (WP15f L3)

webhookdelivery: one row per delivery attempt of one event to one webhook —
status, HTTP code, duration, a short URL-free error, the payload for
redelivery. run_id is a soft reference (the log outlives a deleted run);
webhook_id cascades in the purge/entity-delete plans. Swept after 30 days.

Revision ID: d072b3c4d5fe
Revises: d062b3c4d5fd
Create Date: 2026-10-20 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'd072b3c4d5fe'
down_revision: Union[str, None] = 'd062b3c4d5fd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'webhookdelivery',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('webhook_id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=True),
        sa.Column('event', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('event_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('delivery_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('attempt', sa.Integer(), nullable=False),
        sa.Column('manual', sa.Boolean(), nullable=False),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('status_code', sa.Integer(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('next_retry_at', sa.DateTime(), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['webhook_id'], ['workspacewebhook.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    for col in ('webhook_id', 'workspace_id', 'run_id', 'delivery_id', 'created_at'):
        op.create_index(op.f(f'ix_webhookdelivery_{col}'), 'webhookdelivery', [col], unique=False)


def downgrade() -> None:
    for col in ('created_at', 'delivery_id', 'run_id', 'workspace_id', 'webhook_id'):
        op.drop_index(op.f(f'ix_webhookdelivery_{col}'), table_name='webhookdelivery')
    op.drop_table('webhookdelivery')
