"""manual testing sign-off + project cost budgets (WP12d)

releasesignoff: a person's approve/reject of a project release, scoped to a
set of runs (and optionally cases). projectcostbudget: optional monthly spend
budget per project. costbudgetalert: one row per (project, month, threshold)
— the unique key is the once-per-threshold-per-month dedupe.

Manual cases and manual runs need no schema change: executor is a plain string
column ("manual"), and a tester's verdicts live in TestCaseResult.result_payload.

Revision ID: ce42b3c4d5f4
Revises: ce32b3c4d5f3
Create Date: 2026-10-16 20:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'ce42b3c4d5f4'
down_revision: Union[str, None] = 'ce32b3c4d5f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'releasesignoff',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('release_label', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('git_commit', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('run_ids', sa.JSON(), nullable=True),
        sa.Column('case_ids', sa.JSON(), nullable=True),
        sa.Column('verdict', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('signed_by_id', sa.Integer(), nullable=False),
        sa.Column('signed_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['signed_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_releasesignoff_project_id'), 'releasesignoff', ['project_id'], unique=False)
    op.create_index(op.f('ix_releasesignoff_release_label'), 'releasesignoff', ['release_label'], unique=False)
    op.create_index(op.f('ix_releasesignoff_git_commit'), 'releasesignoff', ['git_commit'], unique=False)
    op.create_index(op.f('ix_releasesignoff_signed_at'), 'releasesignoff', ['signed_at'], unique=False)

    op.create_table(
        'projectcostbudget',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('monthly_amount', sa.Float(), nullable=False),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['updated_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_projectcostbudget_project_id'), 'projectcostbudget', ['project_id'], unique=True)

    op.create_table(
        'costbudgetalert',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('period', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('threshold', sa.Integer(), nullable=False),
        sa.Column('spent', sa.Float(), nullable=False),
        sa.Column('budget', sa.Float(), nullable=False),
        sa.Column('currency', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'period', 'threshold', name='uq_cost_budget_alert'),
    )
    op.create_index(op.f('ix_costbudgetalert_project_id'), 'costbudgetalert', ['project_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_costbudgetalert_project_id'), table_name='costbudgetalert')
    op.drop_table('costbudgetalert')
    op.drop_index(op.f('ix_projectcostbudget_project_id'), table_name='projectcostbudget')
    op.drop_table('projectcostbudget')
    op.drop_index(op.f('ix_releasesignoff_signed_at'), table_name='releasesignoff')
    op.drop_index(op.f('ix_releasesignoff_git_commit'), table_name='releasesignoff')
    op.drop_index(op.f('ix_releasesignoff_release_label'), table_name='releasesignoff')
    op.drop_index(op.f('ix_releasesignoff_project_id'), table_name='releasesignoff')
    op.drop_table('releasesignoff')
