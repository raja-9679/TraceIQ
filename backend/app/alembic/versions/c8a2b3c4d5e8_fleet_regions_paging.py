"""fleet & infrastructure: run regions, multi-region monitors, paging channels,
maintenance windows, API-key IP allowlists, project egress policy

Revision ID: c8a2b3c4d5e8
Revises: c9a2b3c4d5ea
Create Date: 2026-10-15 20:00:00.000000

All new columns are nullable with no default, so existing rows keep exactly
their old behaviour (no region = the global job stream; no CIDRs = any source
address; no network policy = egress not enforced unless the instance forces it).
Foreign keys are unnamed so the generated names match the model's.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'c8a2b3c4d5e8'
down_revision: Union[str, None] = 'c9a2b3c4d5ea'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('testrun', sa.Column('region', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.create_index(op.f('ix_testrun_region'), 'testrun', ['region'], unique=False)
    op.add_column('testschedule', sa.Column('regions', sa.JSON(), nullable=True))
    op.add_column('testschedule', sa.Column('region_states', sa.JSON(), nullable=True))
    op.add_column('monitorcheck', sa.Column('region', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('apikey', sa.Column('allowed_cidrs', sa.JSON(), nullable=True))
    op.add_column('project', sa.Column('network_policy', sa.JSON(), nullable=True))

    op.create_table(
        'pagingchannel',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('secret_encrypted', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('api_region', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('severity', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('on_monitor', sa.Boolean(), nullable=False),
        sa.Column('on_run_failure', sa.Boolean(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('last_sent_at', sa.DateTime(), nullable=True),
        sa.Column('last_error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_pagingchannel_project_id'), 'pagingchannel', ['project_id'], unique=False)

    op.create_table(
        'maintenancewindow',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('starts_at', sa.DateTime(), nullable=True),
        sa.Column('ends_at', sa.DateTime(), nullable=True),
        sa.Column('weekdays', sa.JSON(), nullable=True),
        sa.Column('start_time', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('end_time', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('timezone', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_maintenancewindow_project_id'), 'maintenancewindow', ['project_id'], unique=False)

    op.create_table(
        'pagingevent',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('channel_id', sa.Integer(), nullable=True),
        sa.Column('source', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('action', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('dedup_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('outcome', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('detail', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('run_id', sa.Integer(), nullable=True),
        sa.Column('schedule_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_pagingevent_project_id'), 'pagingevent', ['project_id'], unique=False)
    op.create_index(op.f('ix_pagingevent_channel_id'), 'pagingevent', ['channel_id'], unique=False)
    op.create_index(op.f('ix_pagingevent_dedup_key'), 'pagingevent', ['dedup_key'], unique=False)
    op.create_index(op.f('ix_pagingevent_created_at'), 'pagingevent', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_pagingevent_created_at'), table_name='pagingevent')
    op.drop_index(op.f('ix_pagingevent_dedup_key'), table_name='pagingevent')
    op.drop_index(op.f('ix_pagingevent_channel_id'), table_name='pagingevent')
    op.drop_index(op.f('ix_pagingevent_project_id'), table_name='pagingevent')
    op.drop_table('pagingevent')
    op.drop_index(op.f('ix_maintenancewindow_project_id'), table_name='maintenancewindow')
    op.drop_table('maintenancewindow')
    op.drop_index(op.f('ix_pagingchannel_project_id'), table_name='pagingchannel')
    op.drop_table('pagingchannel')
    op.drop_column('project', 'network_policy')
    op.drop_column('apikey', 'allowed_cidrs')
    op.drop_column('monitorcheck', 'region')
    op.drop_column('testschedule', 'region_states')
    op.drop_column('testschedule', 'regions')
    op.drop_index(op.f('ix_testrun_region'), table_name='testrun')
    op.drop_column('testrun', 'region')
