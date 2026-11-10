"""failure discussion: comments, assignment, notifications

resultcomment: plain-text thread on one result of a run. resultassignment:
who is looking at a failed result (one per result; test_case_id lets the next
run's failure of the same test show the open assignment). notification: what
appears under a person's bell — a kind and ids, no text.

All three are project-keyed and get the same RLS policy shape as e152b3c4d616
(restricts only under 'enforce'); the text is frozen here and checked by
tests/test_rls_plan.py against rls.policy_sql().

Revision ID: e162b3c4d617
Revises: e152b3c4d616
Create Date: 2026-11-10 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'e162b3c4d617'
down_revision: Union[str, None] = 'e152b3c4d616'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

POLICY_NAME = "traceiq_tenant"

POLICIES = {
    'resultcomment': "CASE (SELECT current_setting('traceiq.rls_mode', true)) WHEN 'enforce' THEN COALESCE(((resultcomment.project_id) IN (SELECT p.id FROM project p WHERE p.workspace_id = ANY ((SELECT traceiq_rls_ids('traceiq.workspace_ids'))::int[]) AND (cardinality((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[]) = 0 OR p.id = ANY ((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[])))), false) ELSE true END",
    'resultassignment': "CASE (SELECT current_setting('traceiq.rls_mode', true)) WHEN 'enforce' THEN COALESCE(((resultassignment.project_id) IN (SELECT p.id FROM project p WHERE p.workspace_id = ANY ((SELECT traceiq_rls_ids('traceiq.workspace_ids'))::int[]) AND (cardinality((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[]) = 0 OR p.id = ANY ((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[])))), false) ELSE true END",
    'notification': "CASE (SELECT current_setting('traceiq.rls_mode', true)) WHEN 'enforce' THEN COALESCE(((notification.project_id) IN (SELECT p.id FROM project p WHERE p.workspace_id = ANY ((SELECT traceiq_rls_ids('traceiq.workspace_ids'))::int[]) AND (cardinality((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[]) = 0 OR p.id = ANY ((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[])))), false) ELSE true END",
}

INDEXES = {
    'resultcomment': ('project_id', 'test_run_id', 'result_id'),
    'resultassignment': ('project_id', 'test_run_id', 'result_id', 'test_case_id', 'assignee_id', 'status'),
    'notification': ('user_id', 'project_id', 'result_id', 'created_at'),
}


def upgrade() -> None:
    op.create_table(
        'resultcomment',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('test_run_id', sa.Integer(), nullable=False),
        sa.Column('result_id', sa.Integer(), nullable=False),
        sa.Column('author_id', sa.Integer(), nullable=True),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['author_id'], ['users.id']),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['test_run_id'], ['testrun.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'resultassignment',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('test_run_id', sa.Integer(), nullable=False),
        sa.Column('result_id', sa.Integer(), nullable=False),
        sa.Column('test_case_id', sa.Integer(), nullable=True),
        sa.Column('assignee_id', sa.Integer(), nullable=False),
        sa.Column('assigned_by_id', sa.Integer(), nullable=True),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('done_by_id', sa.Integer(), nullable=True),
        sa.Column('done_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['assigned_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['assignee_id'], ['users.id']),
        sa.ForeignKeyConstraint(['done_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['test_run_id'], ['testrun.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('result_id', name='uq_resultassignment_result'),
    )
    op.create_table(
        'notification',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('actor_id', sa.Integer(), nullable=True),
        sa.Column('run_id', sa.Integer(), nullable=False),
        sa.Column('result_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('read_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id']),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    for table, cols in INDEXES.items():
        for col in cols:
            op.create_index(op.f(f'ix_{table}_{col}'), table, [col], unique=False)
    for table, expr in POLICIES.items():
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY {POLICY_NAME} ON "{table}" FOR ALL '
                   f'USING ({expr}) WITH CHECK ({expr})')


def downgrade() -> None:
    for table in POLICIES:
        op.execute(f'DROP POLICY IF EXISTS {POLICY_NAME} ON "{table}"')
    for table, cols in INDEXES.items():
        for col in reversed(cols):
            op.drop_index(op.f(f'ix_{table}_{col}'), table_name=table)
    op.drop_table('notification')
    op.drop_table('resultassignment')
    op.drop_table('resultcomment')
