"""reporting: journey coverage imports, business journeys, flags, benchmarks (WP12c)

journeyimport / journeyconfig: analytics page-view imports (versioned) and the
project's path rules. businessjourney: named groups of cases. flagcatalogentry:
the project's feature-flag catalogue; testrun.flags: what a run declared.
benchmarkgroup + testrun.benchmark_group_id: one suite against N target URLs.

Revision ID: ce32b3c4d5f3
Revises: ce22b3c4d5f2
Create Date: 2026-10-16 20:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'ce32b3c4d5f3'
down_revision: Union[str, None] = 'ce22b3c4d5f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'journeyimport',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('label', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('filename', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('source_format', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('row_count', sa.Integer(), nullable=False),
        sa.Column('skipped_rows', sa.Integer(), nullable=False),
        sa.Column('path_count', sa.Integer(), nullable=False),
        sa.Column('total_views', sa.Integer(), nullable=False),
        sa.Column('period_start', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('period_end', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('rows', sa.JSON(), nullable=True),
        sa.Column('warnings', sa.JSON(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_journeyimport_project_id'), 'journeyimport', ['project_id'], unique=False)
    op.create_index(op.f('ix_journeyimport_created_at'), 'journeyimport', ['created_at'], unique=False)

    op.create_table(
        'journeyconfig',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('path_rules', sa.JSON(), nullable=True),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['updated_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_journeyconfig_project_id'), 'journeyconfig', ['project_id'], unique=True)

    op.create_table(
        'businessjourney',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('owner_id', sa.Integer(), nullable=True),
        sa.Column('criticality', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('case_ids', sa.JSON(), nullable=True),
        sa.Column('requirement_refs', sa.JSON(), nullable=True),
        sa.Column('traffic_journey', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'name', name='uq_business_journey_name'),
    )
    op.create_index(op.f('ix_businessjourney_project_id'), 'businessjourney', ['project_id'], unique=False)

    op.create_table(
        'flagcatalogentry',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('values', sa.JSON(), nullable=True),
        sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'name', name='uq_flag_catalog_name'),
    )
    op.create_index(op.f('ix_flagcatalogentry_project_id'), 'flagcatalogentry', ['project_id'], unique=False)

    op.create_table(
        'benchmarkgroup',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('test_suite_id', sa.Integer(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('targets', sa.JSON(), nullable=True),
        sa.Column('browser', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['test_suite_id'], ['testsuite.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_benchmarkgroup_project_id'), 'benchmarkgroup', ['project_id'], unique=False)
    op.create_index(op.f('ix_benchmarkgroup_test_suite_id'), 'benchmarkgroup', ['test_suite_id'], unique=False)
    op.create_index(op.f('ix_benchmarkgroup_created_at'), 'benchmarkgroup', ['created_at'], unique=False)

    op.add_column('testrun', sa.Column('flags', sa.JSON(), nullable=True))
    op.add_column('testrun', sa.Column('benchmark_group_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_testrun_benchmark_group_id'), 'testrun', ['benchmark_group_id'], unique=False)
    op.create_foreign_key(None, 'testrun', 'benchmarkgroup', ['benchmark_group_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint('testrun_benchmark_group_id_fkey', 'testrun', type_='foreignkey')
    op.drop_index(op.f('ix_testrun_benchmark_group_id'), table_name='testrun')
    op.drop_column('testrun', 'benchmark_group_id')
    op.drop_column('testrun', 'flags')
    op.drop_index(op.f('ix_benchmarkgroup_created_at'), table_name='benchmarkgroup')
    op.drop_index(op.f('ix_benchmarkgroup_test_suite_id'), table_name='benchmarkgroup')
    op.drop_index(op.f('ix_benchmarkgroup_project_id'), table_name='benchmarkgroup')
    op.drop_table('benchmarkgroup')
    op.drop_index(op.f('ix_flagcatalogentry_project_id'), table_name='flagcatalogentry')
    op.drop_table('flagcatalogentry')
    op.drop_index(op.f('ix_businessjourney_project_id'), table_name='businessjourney')
    op.drop_table('businessjourney')
    op.drop_index(op.f('ix_journeyconfig_project_id'), table_name='journeyconfig')
    op.drop_table('journeyconfig')
    op.drop_index(op.f('ix_journeyimport_created_at'), table_name='journeyimport')
    op.drop_index(op.f('ix_journeyimport_project_id'), table_name='journeyimport')
    op.drop_table('journeyimport')
