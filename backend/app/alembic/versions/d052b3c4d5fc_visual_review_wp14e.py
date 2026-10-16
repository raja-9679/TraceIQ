"""visual review: element baselines, stored comparisons, review comments (WP14e)

visualbaseline.element_key / selector: an element baseline (a selector
screenshot) is keyed apart from the full-page baseline of the same step.
'' is the full-page key, so existing rows keep meaning what they meant.

visualcomparison: one expect-visual-match outcome per run (diff ratio, changed
pixels, change regions) — what the review sorts, filters and groups on.

visualreviewcomment: comment threads on a review item or a group of related
changes.

Revision ID: d052b3c4d5fc
Revises: d032b3c4d5fa
Create Date: 2026-10-16 23:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'd052b3c4d5fc'
down_revision: Union[str, None] = 'd032b3c4d5fa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('visualbaseline', sa.Column(
        'element_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''))
    op.add_column('visualbaseline', sa.Column(
        'selector', sqlmodel.sql.sqltypes.AutoString(), nullable=True))

    op.create_table(
        'visualcomparison',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('test_run_id', sa.Integer(), nullable=False),
        sa.Column('test_case_id', sa.Integer(), nullable=True),
        sa.Column('test_name', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('step_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('browser', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('device', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('element_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('selector', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('baseline_id', sa.Integer(), nullable=True),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('candidate_key', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('diff_key', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('diff_ratio', sa.Float(), nullable=True),
        sa.Column('diff_pixels', sa.Integer(), nullable=True),
        sa.Column('total_pixels', sa.Integer(), nullable=True),
        sa.Column('width', sa.Integer(), nullable=True),
        sa.Column('height', sa.Integer(), nullable=True),
        sa.Column('size_mismatch', sa.Boolean(), nullable=False),
        sa.Column('tolerance', sa.Float(), nullable=True),
        sa.Column('regions', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['test_run_id'], ['testrun.id']),
        sa.ForeignKeyConstraint(['test_case_id'], ['testcase.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_visualcomparison_project_id'), 'visualcomparison', ['project_id'], unique=False)
    op.create_index(op.f('ix_visualcomparison_test_run_id'), 'visualcomparison', ['test_run_id'], unique=False)
    op.create_index(op.f('ix_visualcomparison_test_case_id'), 'visualcomparison', ['test_case_id'], unique=False)

    op.create_table(
        'visualreviewcomment',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('test_run_id', sa.Integer(), nullable=True),
        sa.Column('comparison_id', sa.Integer(), nullable=True),
        sa.Column('group_key', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('author_id', sa.Integer(), nullable=True),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('resolved', sa.Boolean(), nullable=False),
        sa.Column('resolved_by_id', sa.Integer(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['test_run_id'], ['testrun.id']),
        sa.ForeignKeyConstraint(['comparison_id'], ['visualcomparison.id']),
        sa.ForeignKeyConstraint(['author_id'], ['users.id']),
        sa.ForeignKeyConstraint(['resolved_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_visualreviewcomment_project_id'), 'visualreviewcomment', ['project_id'], unique=False)
    op.create_index(op.f('ix_visualreviewcomment_test_run_id'), 'visualreviewcomment', ['test_run_id'], unique=False)
    op.create_index(op.f('ix_visualreviewcomment_comparison_id'), 'visualreviewcomment', ['comparison_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_visualreviewcomment_comparison_id'), table_name='visualreviewcomment')
    op.drop_index(op.f('ix_visualreviewcomment_test_run_id'), table_name='visualreviewcomment')
    op.drop_index(op.f('ix_visualreviewcomment_project_id'), table_name='visualreviewcomment')
    op.drop_table('visualreviewcomment')
    op.drop_index(op.f('ix_visualcomparison_test_case_id'), table_name='visualcomparison')
    op.drop_index(op.f('ix_visualcomparison_test_run_id'), table_name='visualcomparison')
    op.drop_index(op.f('ix_visualcomparison_project_id'), table_name='visualcomparison')
    op.drop_table('visualcomparison')
    op.drop_column('visualbaseline', 'selector')
    op.drop_column('visualbaseline', 'element_key')
