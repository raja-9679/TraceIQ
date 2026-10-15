"""estate & perimeter scanning (WP9)

Revision ID: c9a2b3c4d5ea
Revises: c7a2b3c4d5e7
Create Date: 2026-10-15 20:00:00.000000

Adds estate / estatedomain / estatescan / estatedomainresult (passive domain
checks and score history) and monitoredpage / pagescript /
pagechangeevent (third-party script inventory + defacement monitoring).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'c9a2b3c4d5ea'
down_revision: Union[str, None] = 'c7a2b3c4d5e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_S = sqlmodel.sql.sqltypes.AutoString


def upgrade() -> None:
    op.create_table(
        'estate',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('name', _S(), nullable=False),
        sa.Column('dkim_selectors', sa.JSON(), nullable=True),
        sa.Column('discover_subdomains', sa.Boolean(), nullable=False),
        sa.Column('cron_expression', _S(), nullable=False),
        sa.Column('schedule_enabled', sa.Boolean(), nullable=False),
        sa.Column('next_scan_at', sa.DateTime(), nullable=True),
        sa.Column('last_scan_at', sa.DateTime(), nullable=True),
        sa.Column('cert_alert_days', sa.JSON(), nullable=True),
        sa.Column('alert_emails', sa.JSON(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_estate_project_id'), 'estate', ['project_id'], unique=False)
    op.create_index(op.f('ix_estate_next_scan_at'), 'estate', ['next_scan_at'], unique=False)

    op.create_table(
        'estatedomain',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('estate_id', sa.Integer(), nullable=False),
        sa.Column('hostname', _S(), nullable=False),
        sa.Column('source', _S(), nullable=False),
        sa.Column('is_apex', sa.Boolean(), nullable=True),
        sa.Column('score', sa.Integer(), nullable=True),
        sa.Column('cert_expires_at', sa.DateTime(), nullable=True),
        sa.Column('cert_alert_sent_days', sa.Integer(), nullable=True),
        sa.Column('last_scanned_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['estate_id'], ['estate.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_estatedomain_estate_id'), 'estatedomain', ['estate_id'], unique=False)
    op.create_index(op.f('ix_estatedomain_hostname'), 'estatedomain', ['hostname'], unique=False)

    op.create_table(
        'estatescan',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('estate_id', sa.Integer(), nullable=False),
        sa.Column('status', _S(), nullable=False),
        sa.Column('trigger', _S(), nullable=False),
        sa.Column('summary', sa.JSON(), nullable=True),
        sa.Column('error', _S(), nullable=True),
        sa.Column('requested_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['estate_id'], ['estate.id']),
        sa.ForeignKeyConstraint(['requested_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_estatescan_estate_id'), 'estatescan', ['estate_id'], unique=False)

    op.create_table(
        'estatedomainresult',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('scan_id', sa.Integer(), nullable=False),
        sa.Column('domain_id', sa.Integer(), nullable=False),
        sa.Column('score', sa.Integer(), nullable=False),
        sa.Column('checks', sa.JSON(), nullable=True),
        sa.Column('deductions', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['domain_id'], ['estatedomain.id']),
        sa.ForeignKeyConstraint(['scan_id'], ['estatescan.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_estatedomainresult_scan_id'), 'estatedomainresult', ['scan_id'], unique=False)
    op.create_index(op.f('ix_estatedomainresult_domain_id'), 'estatedomainresult', ['domain_id'], unique=False)
    op.create_index(op.f('ix_estatedomainresult_created_at'), 'estatedomainresult', ['created_at'], unique=False)

    op.create_table(
        'monitoredpage',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('name', _S(), nullable=False),
        sa.Column('url', _S(), nullable=False),
        sa.Column('track_scripts', sa.Boolean(), nullable=False),
        sa.Column('track_content', sa.Boolean(), nullable=False),
        sa.Column('cron_expression', _S(), nullable=False),
        sa.Column('schedule_enabled', sa.Boolean(), nullable=False),
        sa.Column('next_check_at', sa.DateTime(), nullable=True),
        sa.Column('last_checked_at', sa.DateTime(), nullable=True),
        sa.Column('last_status', _S(), nullable=True),
        sa.Column('last_error', _S(), nullable=True),
        sa.Column('headers_baseline', sa.JSON(), nullable=True),
        sa.Column('content_baseline', _S(), nullable=True),
        sa.Column('content_baseline_at', sa.DateTime(), nullable=True),
        sa.Column('similarity_threshold', sa.Float(), nullable=False),
        sa.Column('last_similarity', sa.Float(), nullable=True),
        sa.Column('forbidden_keywords', sa.JSON(), nullable=True),
        sa.Column('element_checks', sa.JSON(), nullable=True),
        sa.Column('alert_emails', sa.JSON(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_monitoredpage_project_id'), 'monitoredpage', ['project_id'], unique=False)
    op.create_index(op.f('ix_monitoredpage_next_check_at'), 'monitoredpage', ['next_check_at'], unique=False)

    op.create_table(
        'pagescript',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('page_id', sa.Integer(), nullable=False),
        sa.Column('script_key', _S(), nullable=False),
        sa.Column('kind', _S(), nullable=False),
        sa.Column('src', _S(), nullable=True),
        sa.Column('sha384', _S(), nullable=True),
        sa.Column('integrity', _S(), nullable=True),
        sa.Column('third_party', sa.Boolean(), nullable=False),
        sa.Column('present', sa.Boolean(), nullable=False),
        sa.Column('first_seen_at', sa.DateTime(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(), nullable=False),
        sa.Column('removed_at', sa.DateTime(), nullable=True),
        sa.Column('authorized', sa.Boolean(), nullable=False),
        sa.Column('justification', _S(), nullable=True),
        sa.Column('authorized_by_id', sa.Integer(), nullable=True),
        sa.Column('authorized_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['authorized_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['page_id'], ['monitoredpage.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_pagescript_page_id'), 'pagescript', ['page_id'], unique=False)

    op.create_table(
        'pagechangeevent',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('page_id', sa.Integer(), nullable=False),
        sa.Column('kind', _S(), nullable=False),
        sa.Column('summary', _S(), nullable=False),
        sa.Column('detail', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['page_id'], ['monitoredpage.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_pagechangeevent_page_id'), 'pagechangeevent', ['page_id'], unique=False)
    op.create_index(op.f('ix_pagechangeevent_created_at'), 'pagechangeevent', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_pagechangeevent_created_at'), table_name='pagechangeevent')
    op.drop_index(op.f('ix_pagechangeevent_page_id'), table_name='pagechangeevent')
    op.drop_table('pagechangeevent')
    op.drop_index(op.f('ix_pagescript_page_id'), table_name='pagescript')
    op.drop_table('pagescript')
    op.drop_index(op.f('ix_monitoredpage_next_check_at'), table_name='monitoredpage')
    op.drop_index(op.f('ix_monitoredpage_project_id'), table_name='monitoredpage')
    op.drop_table('monitoredpage')
    op.drop_index(op.f('ix_estatedomainresult_created_at'), table_name='estatedomainresult')
    op.drop_index(op.f('ix_estatedomainresult_domain_id'), table_name='estatedomainresult')
    op.drop_index(op.f('ix_estatedomainresult_scan_id'), table_name='estatedomainresult')
    op.drop_table('estatedomainresult')
    op.drop_index(op.f('ix_estatescan_estate_id'), table_name='estatescan')
    op.drop_table('estatescan')
    op.drop_index(op.f('ix_estatedomain_hostname'), table_name='estatedomain')
    op.drop_index(op.f('ix_estatedomain_estate_id'), table_name='estatedomain')
    op.drop_table('estatedomain')
    op.drop_index(op.f('ix_estate_next_scan_at'), table_name='estate')
    op.drop_index(op.f('ix_estate_project_id'), table_name='estate')
    op.drop_table('estate')
