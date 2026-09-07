"""Test-case revision history

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-07-26 14:00:00.000000

TestCaseRevision: immutable post-change snapshots of test cases —
history/diff/restore, the safety net for agent-edited tests.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'testcaserevision',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('test_case_id', sa.Integer(), nullable=False),
        sa.Column('revision_number', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('snapshot', sa.JSON(), nullable=False),
        sa.Column('change_source', sa.String(), nullable=False, server_default='update'),
        sa.Column('changed_by_id', sa.Integer(), nullable=True),
        sa.Column('changed_by_agent_id', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['test_case_id'], ['testcase.id']),
        sa.ForeignKeyConstraint(['changed_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_testcaserevision_test_case_id', 'testcaserevision', ['test_case_id'])


def downgrade() -> None:
    op.drop_index('ix_testcaserevision_test_case_id', table_name='testcaserevision')
    op.drop_table('testcaserevision')
