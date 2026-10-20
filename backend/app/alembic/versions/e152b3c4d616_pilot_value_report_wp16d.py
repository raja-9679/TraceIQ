"""pilot value report (WP16d, decision V6)

hypothesisfeedback: one vote per (result, hypothesis, user) on failure-
investigation hypotheses — the reviewed sample behind "AI failure-analysis
accuracy". failurecluster.first_triaged_at: when a person first moved a
cluster to investigating/resolved — a "diagnosed" signal for time to
diagnosis. RLS policy for the new table: same shape as e142b3c4d615
(project-keyed, restricts only under 'enforce'); text frozen, checked by
tests/test_rls_plan.py against rls.policy_sql().

Revision ID: e152b3c4d616
Revises: e142b3c4d615
Create Date: 2026-10-21 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'e152b3c4d616'
down_revision: Union[str, None] = 'e142b3c4d615'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

POLICY_NAME = "traceiq_tenant"

POLICIES = {
    'hypothesisfeedback': "CASE (SELECT current_setting('traceiq.rls_mode', true)) WHEN 'enforce' THEN COALESCE(((hypothesisfeedback.project_id) IN (SELECT p.id FROM project p WHERE p.workspace_id = ANY ((SELECT traceiq_rls_ids('traceiq.workspace_ids'))::int[]) AND (cardinality((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[]) = 0 OR p.id = ANY ((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[])))), false) ELSE true END",
}


def upgrade() -> None:
    op.add_column('failurecluster', sa.Column('first_triaged_at', sa.DateTime(), nullable=True))
    op.create_table(
        'hypothesisfeedback',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=False),
        sa.Column('result_id', sa.Integer(), nullable=False),
        sa.Column('run_created_at', sa.DateTime(), nullable=False),
        sa.Column('hypothesis_index', sa.Integer(), nullable=False),
        sa.Column('hypothesis_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('category', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('source', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('verdict', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('result_id', 'hypothesis_key', 'user_id',
                            name='uq_hypothesisfeedback_result_key_user'),
    )
    for col in ('project_id', 'run_id', 'result_id', 'run_created_at', 'user_id'):
        op.create_index(op.f(f'ix_hypothesisfeedback_{col}'), 'hypothesisfeedback', [col], unique=False)
    for table, expr in POLICIES.items():
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY {POLICY_NAME} ON "{table}" FOR ALL '
                   f'USING ({expr}) WITH CHECK ({expr})')


def downgrade() -> None:
    for table in POLICIES:
        op.execute(f'DROP POLICY IF EXISTS {POLICY_NAME} ON "{table}"')
    for col in ('user_id', 'run_created_at', 'result_id', 'run_id', 'project_id'):
        op.drop_index(op.f(f'ix_hypothesisfeedback_{col}'), table_name='hypothesisfeedback')
    op.drop_table('hypothesisfeedback')
    op.drop_column('failurecluster', 'first_triaged_at')
