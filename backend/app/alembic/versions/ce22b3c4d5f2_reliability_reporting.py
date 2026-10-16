"""delivery + reliability reporting: deployments (DORA), monitor SLO targets,
flake trend snapshots, third-party vendor health roll-ups (WP12b)

Also two read indexes the reports need: testrun (project_id, created_at) and
testcaseresult (test_run_id). Databases migrated through the legacy chain may
already have the second (legacy a7b3c9d2e1f4), hence IF NOT EXISTS; both are
built CONCURRENTLY because testrun/testcaseresult are the largest tables.

Revision ID: ce22b3c4d5f2
Revises: ce12b3c4d5f1
Create Date: 2026-10-16 20:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'ce22b3c4d5f2'
down_revision: Union[str, None] = 'ce12b3c4d5f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'deployment',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('environment_id', sa.Integer(), nullable=True),
        sa.Column('git_commit', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('git_branch', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('git_repo', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('version', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('deployed_at', sa.DateTime(), nullable=False),
        sa.Column('commit_at', sa.DateTime(), nullable=True),
        sa.Column('source', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('external_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('api_key_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['environment_id'], ['projectenvironment.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'external_id', name='uq_deployment_project_external'),
    )
    op.create_index(op.f('ix_deployment_project_id'), 'deployment', ['project_id'], unique=False)
    op.create_index(op.f('ix_deployment_git_commit'), 'deployment', ['git_commit'], unique=False)
    op.create_index(op.f('ix_deployment_deployed_at'), 'deployment', ['deployed_at'], unique=False)

    op.create_table(
        'flakedailysnapshot',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('flaky_cases', sa.Integer(), nullable=False),
        sa.Column('quarantined_cases', sa.Integer(), nullable=False),
        sa.Column('avg_flake_score', sa.Float(), nullable=True),
        sa.Column('results_total', sa.Integer(), nullable=False),
        sa.Column('results_flaky', sa.Integer(), nullable=False),
        sa.Column('results_failed', sa.Integer(), nullable=False),
        sa.Column('computed_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'day', name='uq_flakesnapshot_project_day'),
    )
    op.create_index(op.f('ix_flakedailysnapshot_project_id'), 'flakedailysnapshot', ['project_id'], unique=False)

    op.create_table(
        'flakecasescoredaily',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('test_case_id', sa.Integer(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('flake_score', sa.Float(), nullable=False),
        sa.Column('is_quarantined', sa.Boolean(), nullable=False),
        sa.Column('sample_count', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['test_case_id'], ['testcase.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('test_case_id', 'day', name='uq_flakecasescore_case_day'),
    )
    op.create_index(op.f('ix_flakecasescoredaily_project_id'), 'flakecasescoredaily', ['project_id'], unique=False)
    op.create_index(op.f('ix_flakecasescoredaily_test_case_id'), 'flakecasescoredaily', ['test_case_id'], unique=False)

    op.create_table(
        'vendorhealthdaily',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('vendor', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('requests', sa.Integer(), nullable=False),
        sa.Column('errors', sa.Integer(), nullable=False),
        sa.Column('latency_hist', sa.JSON(), nullable=True),
        sa.Column('latency_p50_ms', sa.Float(), nullable=True),
        sa.Column('latency_p95_ms', sa.Float(), nullable=True),
        sa.Column('hosts', sa.JSON(), nullable=True),
        sa.Column('runs', sa.Integer(), nullable=False),
        sa.Column('runs_with_errors', sa.Integer(), nullable=False),
        sa.Column('failed_results', sa.Integer(), nullable=False),
        sa.Column('failed_results_vendor_errors', sa.Integer(), nullable=False),
        sa.Column('passed_results', sa.Integer(), nullable=False),
        sa.Column('passed_results_vendor_errors', sa.Integer(), nullable=False),
        sa.Column('computed_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'day', 'vendor', name='uq_vendorhealth_project_day_vendor'),
    )
    op.create_index(op.f('ix_vendorhealthdaily_project_id'), 'vendorhealthdaily', ['project_id'], unique=False)
    op.create_index(op.f('ix_vendorhealthdaily_day'), 'vendorhealthdaily', ['day'], unique=False)

    op.add_column('testschedule', sa.Column('slo_availability_target', sa.Float(), nullable=True))
    op.add_column('testschedule', sa.Column('slo_latency_p95_ms', sa.Integer(), nullable=True))
    op.add_column('testschedule', sa.Column('slo_window_days', sa.Integer(), nullable=True))

    with op.get_context().autocommit_block():
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_testrun_project_created "
                   "ON testrun (project_id, created_at)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_testcaseresult_test_run_id "
                   "ON testcaseresult (test_run_id)")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_testcaseresult_test_run_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_testrun_project_created")

    op.drop_column('testschedule', 'slo_window_days')
    op.drop_column('testschedule', 'slo_latency_p95_ms')
    op.drop_column('testschedule', 'slo_availability_target')

    op.drop_index(op.f('ix_vendorhealthdaily_day'), table_name='vendorhealthdaily')
    op.drop_index(op.f('ix_vendorhealthdaily_project_id'), table_name='vendorhealthdaily')
    op.drop_table('vendorhealthdaily')
    op.drop_index(op.f('ix_flakecasescoredaily_test_case_id'), table_name='flakecasescoredaily')
    op.drop_index(op.f('ix_flakecasescoredaily_project_id'), table_name='flakecasescoredaily')
    op.drop_table('flakecasescoredaily')
    op.drop_index(op.f('ix_flakedailysnapshot_project_id'), table_name='flakedailysnapshot')
    op.drop_table('flakedailysnapshot')
    op.drop_index(op.f('ix_deployment_deployed_at'), table_name='deployment')
    op.drop_index(op.f('ix_deployment_git_commit'), table_name='deployment')
    op.drop_index(op.f('ix_deployment_project_id'), table_name='deployment')
    op.drop_table('deployment')
