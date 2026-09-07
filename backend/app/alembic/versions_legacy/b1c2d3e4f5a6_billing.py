"""billing / metering: plans, subscriptions, usage

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5
Create Date: 2026-07-22 22:00:00.000000

Plan/WorkspaceSubscription/UsageRecord + seeded free/pro/enterprise plans
(PLATFORM_VISION.md — commercial readiness / billing blocker).
"""
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import table, column


# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, None] = 'a0b1c2d3e4f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'plan',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('display_name', sa.String(), nullable=False),
        sa.Column('price_cents', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('stripe_price_id', sa.String(), nullable=True),
        sa.Column('limits', sa.JSON(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    op.create_table(
        'workspacesubscription',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('plan_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='active'),
        sa.Column('current_period_start', sa.DateTime(), nullable=True),
        sa.Column('current_period_end', sa.DateTime(), nullable=True),
        sa.Column('stripe_customer_id', sa.String(), nullable=True),
        sa.Column('stripe_subscription_id', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id']),
        sa.ForeignKeyConstraint(['plan_id'], ['plan.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('workspace_id'),
    )
    op.create_index('ix_workspacesubscription_workspace_id', 'workspacesubscription', ['workspace_id'])
    op.create_table(
        'usagerecord',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('period', sa.String(), nullable=False),
        sa.Column('metric', sa.String(), nullable=False),
        sa.Column('count', sa.Integer(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('workspace_id', 'period', 'metric', name='uq_usage_ws_period_metric'),
    )
    op.create_index('ix_usagerecord_workspace_id', 'usagerecord', ['workspace_id'])
    op.create_index('ix_usagerecord_period', 'usagerecord', ['period'])

    # Seed default plans (0 = unlimited).
    plan_t = table('plan',
                   column('name', sa.String), column('display_name', sa.String),
                   column('price_cents', sa.Integer), column('limits', sa.JSON),
                   column('is_active', sa.Boolean))
    op.bulk_insert(plan_t, [
        {'name': 'free', 'display_name': 'Free', 'price_cents': 0, 'is_active': True,
         'limits': {'monthly_runs': 500, 'seats': 3, 'concurrent_runs': 2, 'retention_days': 7, 'ai_daily': 25}},
        {'name': 'pro', 'display_name': 'Pro', 'price_cents': 4900, 'is_active': True,
         'limits': {'monthly_runs': 10000, 'seats': 15, 'concurrent_runs': 8, 'retention_days': 30, 'ai_daily': 200}},
        {'name': 'enterprise', 'display_name': 'Enterprise', 'price_cents': 0, 'is_active': True,
         'limits': {'monthly_runs': 0, 'seats': 0, 'concurrent_runs': 0, 'retention_days': 90, 'ai_daily': 0}},
    ])


def downgrade() -> None:
    op.drop_index('ix_usagerecord_period', table_name='usagerecord')
    op.drop_index('ix_usagerecord_workspace_id', table_name='usagerecord')
    op.drop_table('usagerecord')
    op.drop_index('ix_workspacesubscription_workspace_id', table_name='workspacesubscription')
    op.drop_table('workspacesubscription')
    op.drop_table('plan')
