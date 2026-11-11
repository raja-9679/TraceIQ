"""test cycles

testcycle: a round of testing for one release of a project. testcycleitem:
the cases in it and who is expected to test each. testrun.cycle_id links the
runs made for a cycle (progress is read from their results; nothing is
stored). releasesignoff.cycle_id is a soft reference: the sign-off is
evidence and stays when the cycle is deleted.

Both tables are project-keyed and get the same RLS policy shape as
e162b3c4d617 (restricts only under 'enforce'); the text is frozen here and
checked by tests/test_rls_plan.py against rls.policy_sql().

Revision ID: e172b3c4d618
Revises: e162b3c4d617
Create Date: 2026-11-11 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'e172b3c4d618'
down_revision: Union[str, None] = 'e162b3c4d617'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

POLICY_NAME = "traceiq_tenant"

_EXPR = ("CASE (SELECT current_setting('traceiq.rls_mode', true)) WHEN 'enforce' THEN "
         "COALESCE((({table}.project_id) IN (SELECT p.id FROM project p WHERE p.workspace_id = "
         "ANY ((SELECT traceiq_rls_ids('traceiq.workspace_ids'))::int[]) AND "
         "(cardinality((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[]) = 0 OR p.id = "
         "ANY ((SELECT traceiq_rls_ids('traceiq.project_ids'))::int[])))), false) ELSE true END")

POLICIES = {
    'testcycle': _EXPR.format(table='testcycle'),
    'testcycleitem': _EXPR.format(table='testcycleitem'),
}

INDEXES = {
    'testcycle': ('project_id', 'release_label', 'status'),
    'testcycleitem': ('cycle_id', 'project_id', 'test_case_id', 'assignee_id'),
}


def upgrade() -> None:
    op.create_table(
        'testcycle',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('release_label', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('git_commit', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=True),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('closed_by_id', sa.Integer(), nullable=True),
        sa.Column('closed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['closed_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'testcycleitem',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cycle_id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('test_case_id', sa.Integer(), nullable=False),
        sa.Column('assignee_id', sa.Integer(), nullable=True),
        sa.Column('added_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['added_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['assignee_id'], ['users.id']),
        sa.ForeignKeyConstraint(['cycle_id'], ['testcycle.id']),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['test_case_id'], ['testcase.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cycle_id', 'test_case_id', name='uq_testcycleitem_cycle_case'),
    )
    for table, cols in INDEXES.items():
        for col in cols:
            op.create_index(op.f(f'ix_{table}_{col}'), table, [col], unique=False)
    op.add_column('testrun', sa.Column('cycle_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_testrun_cycle_id'), 'testrun', ['cycle_id'], unique=False)
    op.create_foreign_key(None, 'testrun', 'testcycle', ['cycle_id'], ['id'])
    op.add_column('releasesignoff', sa.Column('cycle_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_releasesignoff_cycle_id'), 'releasesignoff', ['cycle_id'], unique=False)
    for table, expr in POLICIES.items():
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY {POLICY_NAME} ON "{table}" FOR ALL '
                   f'USING ({expr}) WITH CHECK ({expr})')


def downgrade() -> None:
    for table in POLICIES:
        op.execute(f'DROP POLICY IF EXISTS {POLICY_NAME} ON "{table}"')
    op.drop_index(op.f('ix_releasesignoff_cycle_id'), table_name='releasesignoff')
    op.drop_column('releasesignoff', 'cycle_id')
    op.drop_constraint('testrun_cycle_id_fkey', 'testrun', type_='foreignkey')
    op.drop_index(op.f('ix_testrun_cycle_id'), table_name='testrun')
    op.drop_column('testrun', 'cycle_id')
    for table, cols in INDEXES.items():
        for col in reversed(cols):
            op.drop_index(op.f(f'ix_{table}_{col}'), table_name=table)
    op.drop_table('testcycleitem')
    op.drop_table('testcycle')
