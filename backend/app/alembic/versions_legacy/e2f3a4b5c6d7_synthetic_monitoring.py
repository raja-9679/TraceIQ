"""synthetic monitoring: monitor schedules + health checks

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-07-22 13:00:00.000000

Turns scheduled suites into production monitors (PLATFORM_VISION.md §5):
- testschedule gains monitor config (is_monitor, alert_after_failures,
  alert_on_recovery) + server-maintained state (monitor_state,
  last_alert_state, last_checked_at).
- monitorcheck records each monitor-triggered run's outcome — the source of
  truth for uptime/SLA and the consecutive-failure streak that drives alerts.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2f3a4b5c6d7'
down_revision: Union[str, None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('testschedule', sa.Column(
        'is_monitor', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('testschedule', sa.Column(
        'alert_after_failures', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('testschedule', sa.Column(
        'alert_on_recovery', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column('testschedule', sa.Column(
        'monitor_state', sa.String(), nullable=True))
    op.add_column('testschedule', sa.Column(
        'last_alert_state', sa.String(), nullable=True))
    op.add_column('testschedule', sa.Column(
        'last_checked_at', sa.DateTime(), nullable=True))

    op.create_table(
        'monitorcheck',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('schedule_id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('is_up', sa.Boolean(), nullable=False),
        sa.Column('checked_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['schedule_id'], ['testschedule.id']),
        sa.ForeignKeyConstraint(['run_id'], ['testrun.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_monitorcheck_schedule_id', 'monitorcheck', ['schedule_id'])
    op.create_index('ix_monitorcheck_checked_at', 'monitorcheck', ['checked_at'])


def downgrade() -> None:
    op.drop_index('ix_monitorcheck_checked_at', table_name='monitorcheck')
    op.drop_index('ix_monitorcheck_schedule_id', table_name='monitorcheck')
    op.drop_table('monitorcheck')
    op.drop_column('testschedule', 'last_checked_at')
    op.drop_column('testschedule', 'last_alert_state')
    op.drop_column('testschedule', 'monitor_state')
    op.drop_column('testschedule', 'alert_on_recovery')
    op.drop_column('testschedule', 'alert_after_failures')
    op.drop_column('testschedule', 'is_monitor')
