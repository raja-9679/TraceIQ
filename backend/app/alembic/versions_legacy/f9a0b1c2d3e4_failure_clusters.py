"""failure triage clusters

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-07-22 20:00:00.000000

Failure triage / de-duplication (PLATFORM_VISION.md §5, item 2):
- failurecluster: one root-cause signature per project (unique).
- testcaseresult.cluster_id + issueticket.cluster_id link results/tickets to it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f9a0b1c2d3e4'
down_revision: Union[str, None] = 'e8f9a0b1c2d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'failurecluster',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('signature', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('category', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='open'),
        sa.Column('occurrence_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('first_seen_at', sa.DateTime(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(), nullable=False),
        sa.Column('last_run_id', sa.Integer(), nullable=True),
        sa.Column('sample_error', sa.Text(), nullable=True),
        sa.Column('assignee_id', sa.Integer(), nullable=True),
        sa.Column('resolution_note', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['assignee_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'signature', name='uq_cluster_project_signature'),
    )
    op.create_index('ix_failurecluster_project_id', 'failurecluster', ['project_id'])
    op.create_index('ix_failurecluster_signature', 'failurecluster', ['signature'])

    op.add_column('testcaseresult', sa.Column('cluster_id', sa.Integer(), nullable=True))
    op.create_index('ix_testcaseresult_cluster_id', 'testcaseresult', ['cluster_id'])
    op.create_foreign_key('fk_testcaseresult_cluster', 'testcaseresult', 'failurecluster',
                          ['cluster_id'], ['id'], ondelete='SET NULL')

    op.add_column('issueticket', sa.Column('cluster_id', sa.Integer(), nullable=True))
    op.create_index('ix_issueticket_cluster_id', 'issueticket', ['cluster_id'])


def downgrade() -> None:
    op.drop_index('ix_issueticket_cluster_id', table_name='issueticket')
    op.drop_column('issueticket', 'cluster_id')
    op.drop_constraint('fk_testcaseresult_cluster', 'testcaseresult', type_='foreignkey')
    op.drop_index('ix_testcaseresult_cluster_id', table_name='testcaseresult')
    op.drop_column('testcaseresult', 'cluster_id')
    op.drop_index('ix_failurecluster_signature', table_name='failurecluster')
    op.drop_index('ix_failurecluster_project_id', table_name='failurecluster')
    op.drop_table('failurecluster')
