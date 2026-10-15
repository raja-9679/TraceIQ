"""frontend quality: run emulation/sweeps, site crawler, page budgets (WP10)

Revision ID: caa2b3c4d5eb
Revises: c8a2b3c4d5e8
Create Date: 2026-10-15 21:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'caa2b3c4d5eb'
down_revision: Union[str, None] = 'c8a2b3c4d5e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('testrun', sa.Column('emulation', sa.JSON(), nullable=True))
    op.add_column('testrun', sa.Column('sweep_group', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.create_index(op.f('ix_testrun_sweep_group'), 'testrun', ['sweep_group'], unique=False)

    op.create_table('sitecrawl',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('start_url', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('max_depth', sa.Integer(), nullable=False),
    sa.Column('max_pages', sa.Integer(), nullable=False),
    sa.Column('extra_hosts', sa.JSON(), nullable=True),
    sa.Column('respect_robots', sa.Boolean(), nullable=False),
    sa.Column('rate_limit_ms', sa.Integer(), nullable=False),
    sa.Column('slow_threshold_ms', sa.Integer(), nullable=False),
    sa.Column('check_external', sa.Boolean(), nullable=False),
    sa.Column('cron_expression', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('next_run_at', sa.DateTime(), nullable=True),
    sa.Column('last_run_at', sa.DateTime(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sitecrawl_project_id'), 'sitecrawl', ['project_id'], unique=False)
    op.create_index(op.f('ix_sitecrawl_next_run_at'), 'sitecrawl', ['next_run_at'], unique=False)

    op.create_table('sitecrawlrun',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('crawl_id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('trigger', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('config', sa.JSON(), nullable=True),
    sa.Column('requested_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.Column('pages_crawled', sa.Integer(), nullable=False),
    sa.Column('links_checked', sa.Integer(), nullable=False),
    sa.Column('issue_counts', sa.JSON(), nullable=True),
    sa.Column('pages', sa.JSON(), nullable=True),
    sa.Column('robots', sa.JSON(), nullable=True),
    sa.Column('truncated', sa.Boolean(), nullable=False),
    sa.Column('error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.ForeignKeyConstraint(['crawl_id'], ['sitecrawl.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
    sa.ForeignKeyConstraint(['requested_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sitecrawlrun_crawl_id'), 'sitecrawlrun', ['crawl_id'], unique=False)
    op.create_index(op.f('ix_sitecrawlrun_project_id'), 'sitecrawlrun', ['project_id'], unique=False)
    op.create_index(op.f('ix_sitecrawlrun_created_at'), 'sitecrawlrun', ['created_at'], unique=False)

    op.create_table('sitecrawlissue',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('crawl_run_id', sa.Integer(), nullable=False),
    sa.Column('page_url', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('target_url', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('element', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('category', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('status_code', sa.Integer(), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('redirect_chain', sa.JSON(), nullable=True),
    sa.Column('detail', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.ForeignKeyConstraint(['crawl_run_id'], ['sitecrawlrun.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sitecrawlissue_crawl_run_id'), 'sitecrawlissue', ['crawl_run_id'], unique=False)
    op.create_index(op.f('ix_sitecrawlissue_category'), 'sitecrawlissue', ['category'], unique=False)

    op.create_table('pagebudgetsample',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('test_run_id', sa.Integer(), nullable=False),
    sa.Column('test_case_id', sa.Integer(), nullable=True),
    sa.Column('test_name', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('step_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('page_url', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('page_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('variant', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('metrics', sa.JSON(), nullable=True),
    sa.Column('checks', sa.JSON(), nullable=True),
    sa.Column('seo', sa.JSON(), nullable=True),
    sa.Column('passed', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
    sa.ForeignKeyConstraint(['test_case_id'], ['testcase.id'], ),
    sa.ForeignKeyConstraint(['test_run_id'], ['testrun.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_pagebudgetsample_project_id'), 'pagebudgetsample', ['project_id'], unique=False)
    op.create_index(op.f('ix_pagebudgetsample_test_run_id'), 'pagebudgetsample', ['test_run_id'], unique=False)
    op.create_index(op.f('ix_pagebudgetsample_page_key'), 'pagebudgetsample', ['page_key'], unique=False)
    op.create_index(op.f('ix_pagebudgetsample_created_at'), 'pagebudgetsample', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_pagebudgetsample_created_at'), table_name='pagebudgetsample')
    op.drop_index(op.f('ix_pagebudgetsample_page_key'), table_name='pagebudgetsample')
    op.drop_index(op.f('ix_pagebudgetsample_test_run_id'), table_name='pagebudgetsample')
    op.drop_index(op.f('ix_pagebudgetsample_project_id'), table_name='pagebudgetsample')
    op.drop_table('pagebudgetsample')
    op.drop_index(op.f('ix_sitecrawlissue_category'), table_name='sitecrawlissue')
    op.drop_index(op.f('ix_sitecrawlissue_crawl_run_id'), table_name='sitecrawlissue')
    op.drop_table('sitecrawlissue')
    op.drop_index(op.f('ix_sitecrawlrun_created_at'), table_name='sitecrawlrun')
    op.drop_index(op.f('ix_sitecrawlrun_project_id'), table_name='sitecrawlrun')
    op.drop_index(op.f('ix_sitecrawlrun_crawl_id'), table_name='sitecrawlrun')
    op.drop_table('sitecrawlrun')
    op.drop_index(op.f('ix_sitecrawl_next_run_at'), table_name='sitecrawl')
    op.drop_index(op.f('ix_sitecrawl_project_id'), table_name='sitecrawl')
    op.drop_table('sitecrawl')
    op.drop_index(op.f('ix_testrun_sweep_group'), table_name='testrun')
    op.drop_column('testrun', 'sweep_group')
    op.drop_column('testrun', 'emulation')
