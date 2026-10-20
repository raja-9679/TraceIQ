"""accessibility register + uploaded HARs (WP15d H6/H7)

accessibilitycheck: one row per check-accessibility step outcome (the
evidence a page was scanned, clean or not). accessibilityviolation: one row
per (rule, redacted target) — never page text. accessibilitywaiver: expiring
known-issue waivers. hararchive: redacted HAR uploads for har-replay
(object key har/{project_id}/...). Run/case FKs follow the entity-delete plan.

Revision ID: e042b3c4d604
Revises: e112b3c4d611
Create Date: 2026-10-20 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'e042b3c4d604'
down_revision: Union[str, None] = 'e112b3c4d611'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_S = sqlmodel.sql.sqltypes.AutoString


def upgrade() -> None:
    op.create_table(
        'accessibilitycheck',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('test_run_id', sa.Integer(), nullable=False),
        sa.Column('test_case_id', sa.Integer(), nullable=True),
        sa.Column('test_name', _S(), nullable=True),
        sa.Column('step_id', _S(), nullable=True),
        sa.Column('page_key', _S(), nullable=True),
        sa.Column('browser', _S(), nullable=False),
        sa.Column('device', _S(), nullable=True),
        sa.Column('violation_count', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['test_run_id'], ['testrun.id']),
        sa.ForeignKeyConstraint(['test_case_id'], ['testcase.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    for col in ('project_id', 'test_run_id', 'test_case_id', 'created_at'):
        op.create_index(op.f(f'ix_accessibilitycheck_{col}'), 'accessibilitycheck', [col], unique=False)

    op.create_table(
        'accessibilityviolation',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('test_run_id', sa.Integer(), nullable=False),
        sa.Column('test_case_id', sa.Integer(), nullable=True),
        sa.Column('test_name', _S(), nullable=True),
        sa.Column('step_id', _S(), nullable=True),
        sa.Column('page_key', _S(), nullable=True),
        sa.Column('rule_id', _S(), nullable=False),
        sa.Column('impact', _S(), nullable=False),
        sa.Column('target', _S(), nullable=False),
        sa.Column('fingerprint', _S(), nullable=False),
        sa.Column('node_count', sa.Integer(), nullable=False),
        sa.Column('help_url', _S(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['test_run_id'], ['testrun.id']),
        sa.ForeignKeyConstraint(['test_case_id'], ['testcase.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    for col in ('project_id', 'test_run_id', 'test_case_id', 'rule_id', 'fingerprint'):
        op.create_index(op.f(f'ix_accessibilityviolation_{col}'), 'accessibilityviolation', [col],
                        unique=False)

    op.create_table(
        'accessibilitywaiver',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('rule_id', _S(), nullable=False),
        sa.Column('selector_pattern', _S(), nullable=False),
        sa.Column('reason', _S(), nullable=False),
        sa.Column('owner_user_id', sa.Integer(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_accessibilitywaiver_project_id'), 'accessibilitywaiver',
                    ['project_id'], unique=False)

    op.create_table(
        'hararchive',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('name', _S(), nullable=False),
        sa.Column('object_key', _S(), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('entry_count', sa.Integer(), nullable=False),
        sa.Column('hosts', sa.JSON(), nullable=True),
        sa.Column('sha256', _S(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_hararchive_project_id'), 'hararchive', ['project_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_hararchive_project_id'), table_name='hararchive')
    op.drop_table('hararchive')
    op.drop_index(op.f('ix_accessibilitywaiver_project_id'), table_name='accessibilitywaiver')
    op.drop_table('accessibilitywaiver')
    for col in ('fingerprint', 'rule_id', 'test_case_id', 'test_run_id', 'project_id'):
        op.drop_index(op.f(f'ix_accessibilityviolation_{col}'), table_name='accessibilityviolation')
    op.drop_table('accessibilityviolation')
    for col in ('created_at', 'test_case_id', 'test_run_id', 'project_id'):
        op.drop_index(op.f(f'ix_accessibilitycheck_{col}'), table_name='accessibilitycheck')
    op.drop_table('accessibilitycheck')
