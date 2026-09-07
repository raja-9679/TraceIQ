"""security findings (passive scan)

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-07-22 14:00:00.000000

The unified findings result-model (PLATFORM_VISION.md P-4). Phase 1 fills it
from passive analysis of already-captured responses; the ZAP/nuclei executors
reuse it later. run_id is ON DELETE CASCADE so purging a run drops its findings.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a4b5c6d7e8'
down_revision: Union[str, None] = 'e2f3a4b5c6d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'securityfinding',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('scan_type', sa.String(), nullable=False, server_default='passive'),
        sa.Column('category', sa.String(), nullable=False),
        sa.Column('severity', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('description', sa.String(), nullable=True),
        sa.Column('evidence', sa.String(), nullable=True),
        sa.Column('target_url', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['run_id'], ['testrun.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_securityfinding_run_id', 'securityfinding', ['run_id'])
    op.create_index('ix_securityfinding_project_id', 'securityfinding', ['project_id'])


def downgrade() -> None:
    op.drop_index('ix_securityfinding_project_id', table_name='securityfinding')
    op.drop_index('ix_securityfinding_run_id', table_name='securityfinding')
    op.drop_table('securityfinding')
