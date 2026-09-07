"""requirement/ticket traceability links

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-07-23 00:00:00.000000

Lightweight test↔requirement links (PLATFORM_VISION.md §5, item 5).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e4f5a6b7c8d9'
down_revision: Union[str, None] = 'd3e4f5a6b7c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'requirementlink',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('test_case_id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('ref', sa.String(), nullable=False),
        sa.Column('source', sa.String(), nullable=False, server_default='manual'),
        sa.Column('title', sa.String(), nullable=True),
        sa.Column('url', sa.String(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['test_case_id'], ['testcase.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('test_case_id', 'ref', name='uq_reqlink_case_ref'),
    )
    op.create_index('ix_requirementlink_test_case_id', 'requirementlink', ['test_case_id'])
    op.create_index('ix_requirementlink_project_id', 'requirementlink', ['project_id'])
    op.create_index('ix_requirementlink_ref', 'requirementlink', ['ref'])


def downgrade() -> None:
    op.drop_index('ix_requirementlink_ref', table_name='requirementlink')
    op.drop_index('ix_requirementlink_project_id', table_name='requirementlink')
    op.drop_index('ix_requirementlink_test_case_id', table_name='requirementlink')
    op.drop_table('requirementlink')
