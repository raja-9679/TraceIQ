"""issue-tracker / defect integration

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-07-22 19:00:00.000000

Jira/iTop/GitHub ticket creation from runs (with artifact upload):
- issuetrackerconfig: workspace-scoped tracker connection (encrypted creds).
- issueticket: a ticket created from a run/result, with external key/url + status.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8f9a0b1c2d3'
down_revision: Union[str, None] = 'd7e8f9a0b1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'issuetrackerconfig',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('provider', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('base_url', sa.String(), nullable=False),
        sa.Column('auth_user', sa.String(), nullable=True),
        sa.Column('auth_secret_encrypted', sa.String(), nullable=False),
        sa.Column('settings', sa.JSON(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_issuetrackerconfig_workspace_id', 'issuetrackerconfig', ['workspace_id'])

    op.create_table(
        'issueticket',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('config_id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=True),
        sa.Column('result_id', sa.Integer(), nullable=True),
        sa.Column('provider', sa.String(), nullable=False),
        sa.Column('external_key', sa.String(), nullable=True),
        sa.Column('url', sa.String(), nullable=True),
        sa.Column('summary', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='pending'),
        sa.Column('attachments_uploaded', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('attachments_total', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error', sa.String(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['config_id'], ['issuetrackerconfig.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id']),
        sa.ForeignKeyConstraint(['run_id'], ['testrun.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_issueticket_config_id', 'issueticket', ['config_id'])
    op.create_index('ix_issueticket_workspace_id', 'issueticket', ['workspace_id'])
    op.create_index('ix_issueticket_run_id', 'issueticket', ['run_id'])


def downgrade() -> None:
    op.drop_index('ix_issueticket_run_id', table_name='issueticket')
    op.drop_index('ix_issueticket_workspace_id', table_name='issueticket')
    op.drop_index('ix_issueticket_config_id', table_name='issueticket')
    op.drop_table('issueticket')
    op.drop_index('ix_issuetrackerconfig_workspace_id', table_name='issuetrackerconfig')
    op.drop_table('issuetrackerconfig')
