"""reporting: share links + digest recipients

ShareLink: an expiring, revocable public link to a run report or an
executive summary; only the SHA-256 of its token is stored.
ReportSchedule.recipient_user_ids: picked project members (access re-checked
at send). ReportSchedule.last_result: what the last delivery did. Existing
`recipients` stay as they are and are treated as extra addresses.

Revision ID: ce12b3c4d5f1
Revises: cbd2b3c4d5ef
Create Date: 2026-10-16 19:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'ce12b3c4d5f1'
down_revision: Union[str, None] = 'cbd2b3c4d5ef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('reportschedule', sa.Column('recipient_user_ids', sa.JSON(), nullable=True))
    op.add_column('reportschedule', sa.Column('last_result', sa.JSON(), nullable=True))
    op.create_table(
        'sharelink',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('target_id', sa.Integer(), nullable=True),
        sa.Column('window_days', sa.Integer(), nullable=False),
        sa.Column('label', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('token_hash', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('last_viewed_at', sa.DateTime(), nullable=True),
        sa.Column('view_count', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_sharelink_workspace_id'), 'sharelink', ['workspace_id'], unique=False)
    op.create_index(op.f('ix_sharelink_project_id'), 'sharelink', ['project_id'], unique=False)
    op.create_index(op.f('ix_sharelink_token_hash'), 'sharelink', ['token_hash'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_sharelink_token_hash'), table_name='sharelink')
    op.drop_index(op.f('ix_sharelink_project_id'), table_name='sharelink')
    op.drop_index(op.f('ix_sharelink_workspace_id'), table_name='sharelink')
    op.drop_table('sharelink')
    op.drop_column('reportschedule', 'last_result')
    op.drop_column('reportschedule', 'recipient_user_ids')
