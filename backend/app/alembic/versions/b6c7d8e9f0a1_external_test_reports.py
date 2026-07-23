"""External CI test reports (JUnit ingestion)

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
Create Date: 2026-07-23 12:00:00.000000

Teams push their own CI's JUnit XML per commit; the quality gate can require
those results to be green (`require_external_tests_pass` policy key).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6c7d8e9f0a1'
down_revision: Union[str, None] = 'a5b6c7d8e9f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'externaltestreport',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(), nullable=False, server_default='junit'),
        sa.Column('suite_name', sa.String(), nullable=True),
        sa.Column('git_commit', sa.String(), nullable=True),
        sa.Column('git_branch', sa.String(), nullable=True),
        sa.Column('tests', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('failures', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('errors', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('skipped', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('time_seconds', sa.Float(), nullable=False, server_default='0'),
        sa.Column('failed_cases', sa.JSON(), nullable=True),
        sa.Column('uploaded_by', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_externaltestreport_project_id', 'externaltestreport', ['project_id'])
    op.create_index('ix_externaltestreport_git_commit', 'externaltestreport', ['git_commit'])
    op.create_index('ix_externaltestreport_created_at', 'externaltestreport', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_externaltestreport_created_at', table_name='externaltestreport')
    op.drop_index('ix_externaltestreport_git_commit', table_name='externaltestreport')
    op.drop_index('ix_externaltestreport_project_id', table_name='externaltestreport')
    op.drop_table('externaltestreport')
