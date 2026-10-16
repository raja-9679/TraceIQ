"""compliance controls, evidence, exceptions, evidence packs, restore drills (WP13a)

compliancecontrol: a workspace's controls (framework/reference/owner/evidence
sources/freshness). controlevidence: manual evidence (note and/or file).
controlexception: four-eyes, expiring waivers. evidencepack: generated zip
packs. drillresult: restore-drill results reported by scripts/restore_drill.sh
(instance-wide, no workspace).

Revision ID: cf12b3c4d5f5
Revises: ce42b3c4d5f4
Create Date: 2026-10-16 22:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'cf12b3c4d5f5'
down_revision: Union[str, None] = 'ce42b3c4d5f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STR = sqlmodel.sql.sqltypes.AutoString


def upgrade() -> None:
    op.create_table(
        'compliancecontrol',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('framework', _STR(), nullable=False),
        sa.Column('reference', _STR(), nullable=False),
        sa.Column('title', _STR(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('owner_id', sa.Integer(), nullable=True),
        sa.Column('evidence_sources', sa.JSON(), nullable=True),
        sa.Column('freshness_days', sa.Integer(), nullable=False),
        sa.Column('seed_key', _STR(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id']),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('workspace_id', 'seed_key', name='uq_compliance_control_seed'),
    )
    op.create_index(op.f('ix_compliancecontrol_workspace_id'), 'compliancecontrol', ['workspace_id'], unique=False)
    op.create_index(op.f('ix_compliancecontrol_framework'), 'compliancecontrol', ['framework'], unique=False)

    op.create_table(
        'controlevidence',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('control_id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('outcome', _STR(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('file_key', _STR(), nullable=True),
        sa.Column('file_name', _STR(), nullable=True),
        sa.Column('content_type', _STR(), nullable=True),
        sa.Column('size_bytes', sa.Integer(), nullable=True),
        sa.Column('sha256', _STR(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['control_id'], ['compliancecontrol.id']),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_controlevidence_control_id'), 'controlevidence', ['control_id'], unique=False)
    op.create_index(op.f('ix_controlevidence_workspace_id'), 'controlevidence', ['workspace_id'], unique=False)
    op.create_index(op.f('ix_controlevidence_created_at'), 'controlevidence', ['created_at'], unique=False)

    op.create_table(
        'controlexception',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('control_id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('compensating_control', sa.Text(), nullable=True),
        sa.Column('status', _STR(), nullable=False),
        sa.Column('requested_by_id', sa.Integer(), nullable=False),
        sa.Column('requested_at', sa.DateTime(), nullable=False),
        sa.Column('approved_by_id', sa.Integer(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('decided_note', sa.Text(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['control_id'], ['compliancecontrol.id']),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id']),
        sa.ForeignKeyConstraint(['requested_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['approved_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_controlexception_control_id'), 'controlexception', ['control_id'], unique=False)
    op.create_index(op.f('ix_controlexception_workspace_id'), 'controlexception', ['workspace_id'], unique=False)

    op.create_table(
        'evidencepack',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('requested_by_id', sa.Integer(), nullable=False),
        sa.Column('period_start', sa.DateTime(), nullable=False),
        sa.Column('period_end', sa.DateTime(), nullable=False),
        sa.Column('status', _STR(), nullable=False),
        sa.Column('object_key', _STR(), nullable=True),
        sa.Column('size_bytes', sa.Integer(), nullable=True),
        sa.Column('sha256', _STR(), nullable=True),
        sa.Column('file_count', sa.Integer(), nullable=True),
        sa.Column('manifest', sa.JSON(), nullable=True),
        sa.Column('error', _STR(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id']),
        sa.ForeignKeyConstraint(['requested_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_evidencepack_workspace_id'), 'evidencepack', ['workspace_id'], unique=False)
    op.create_index(op.f('ix_evidencepack_created_at'), 'evidencepack', ['created_at'], unique=False)

    op.create_table(
        'drillresult',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('ran_at', sa.DateTime(), nullable=False),
        sa.Column('received_at', sa.DateTime(), nullable=False),
        sa.Column('status', _STR(), nullable=False),
        sa.Column('dump_name', _STR(), nullable=True),
        sa.Column('target', _STR(), nullable=True),
        sa.Column('duration_seconds', sa.Integer(), nullable=True),
        sa.Column('checks', sa.JSON(), nullable=True),
        sa.Column('reported_by_id', sa.Integer(), nullable=True),
        sa.Column('reporter_label', _STR(), nullable=True),
        sa.ForeignKeyConstraint(['reported_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_drillresult_ran_at'), 'drillresult', ['ran_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_drillresult_ran_at'), table_name='drillresult')
    op.drop_table('drillresult')
    op.drop_index(op.f('ix_evidencepack_created_at'), table_name='evidencepack')
    op.drop_index(op.f('ix_evidencepack_workspace_id'), table_name='evidencepack')
    op.drop_table('evidencepack')
    op.drop_index(op.f('ix_controlexception_workspace_id'), table_name='controlexception')
    op.drop_index(op.f('ix_controlexception_control_id'), table_name='controlexception')
    op.drop_table('controlexception')
    op.drop_index(op.f('ix_controlevidence_created_at'), table_name='controlevidence')
    op.drop_index(op.f('ix_controlevidence_workspace_id'), table_name='controlevidence')
    op.drop_index(op.f('ix_controlevidence_control_id'), table_name='controlevidence')
    op.drop_table('controlevidence')
    op.drop_index(op.f('ix_compliancecontrol_framework'), table_name='compliancecontrol')
    op.drop_index(op.f('ix_compliancecontrol_workspace_id'), table_name='compliancecontrol')
    op.drop_table('compliancecontrol')
