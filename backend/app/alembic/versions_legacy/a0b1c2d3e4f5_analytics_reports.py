"""analytics: cluster resolved_at + scheduled reports

Revision ID: a0b1c2d3e4f5
Revises: f9a0b1c2d3e4
Create Date: 2026-07-22 21:00:00.000000

- failurecluster.resolved_at → real MTTR (set when status becomes resolved).
- reportschedule → recurring quality reports (PLATFORM_VISION.md §5, item 4).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a0b1c2d3e4f5'
down_revision: Union[str, None] = 'f9a0b1c2d3e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('failurecluster', sa.Column('resolved_at', sa.DateTime(), nullable=True))

    op.create_table(
        'reportschedule',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('cron_expression', sa.String(), nullable=False),
        sa.Column('window_days', sa.Integer(), nullable=False, server_default='7'),
        sa.Column('channels', sa.JSON(), nullable=True),
        sa.Column('recipients', sa.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('next_run_at', sa.DateTime(), nullable=True),
        sa.Column('last_run_at', sa.DateTime(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_reportschedule_project_id', 'reportschedule', ['project_id'])


def downgrade() -> None:
    op.drop_index('ix_reportschedule_project_id', table_name='reportschedule')
    op.drop_table('reportschedule')
    op.drop_column('failurecluster', 'resolved_at')
