"""inbound integrations: imported requirements, incidents, incident webhooks

Revision ID: c6a2b3c4d5e6
Revises: b8c9d0e1f2a3
Create Date: 2026-10-15 18:00:00.000000

Three new tables, nothing changed in existing ones. Foreign keys are unnamed so
the generated names match the model's (a named one shows up as drift in
`alembic check`).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'c6a2b3c4d5e6'
down_revision: Union[str, None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'requirement',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('tracker_config_id', sa.Integer(), nullable=True),
        sa.Column('source', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('external_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('external_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('external_status', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('acceptance_criteria', sa.JSON(), nullable=True),
        sa.Column('external_updated_at', sa.DateTime(), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(), nullable=False),
        sa.Column('sync_error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['tracker_config_id'], ['issuetrackerconfig.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'source', 'external_key',
                            name='uq_requirement_project_source_key'),
    )
    op.create_index(op.f('ix_requirement_external_key'), 'requirement', ['external_key'], unique=False)
    op.create_index(op.f('ix_requirement_project_id'), 'requirement', ['project_id'], unique=False)
    op.create_index(op.f('ix_requirement_tracker_config_id'), 'requirement', ['tracker_config_id'], unique=False)

    op.create_table(
        'incident',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('source', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('external_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('severity', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('service', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('affected_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('git_commit', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('git_branch', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('release', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('fingerprint', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('recurrence_count', sa.Integer(), nullable=False),
        sa.Column('previous_incident_id', sa.Integer(), nullable=True),
        sa.Column('matched_cluster_id', sa.Integer(), nullable=True),
        sa.Column('matched_case_id', sa.Integer(), nullable=True),
        sa.Column('match_reason', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('regression_proposal_id', sa.Integer(), nullable=True),
        sa.Column('regression_status', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('regression_case_id', sa.Integer(), nullable=True),
        sa.Column('escaped_defect', sa.Boolean(), nullable=False),
        sa.Column('escaped_case_id', sa.Integer(), nullable=True),
        sa.Column('escaped_no_test', sa.Boolean(), nullable=False),
        sa.Column('escaped_note', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('escaped_marked_at', sa.DateTime(), nullable=True),
        sa.Column('escaped_marked_by_id', sa.Integer(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['escaped_case_id'], ['testcase.id']),
        sa.ForeignKeyConstraint(['escaped_marked_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['matched_case_id'], ['testcase.id']),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['regression_case_id'], ['testcase.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'source', 'external_id',
                            name='uq_incident_project_source_external'),
    )
    op.create_index(op.f('ix_incident_escaped_defect'), 'incident', ['escaped_defect'], unique=False)
    op.create_index(op.f('ix_incident_fingerprint'), 'incident', ['fingerprint'], unique=False)
    op.create_index(op.f('ix_incident_project_id'), 'incident', ['project_id'], unique=False)
    op.create_index(op.f('ix_incident_status'), 'incident', ['status'], unique=False)

    op.create_table(
        'incidentwebhook',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('secret_encrypted', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('default_suite_id', sa.Integer(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('last_received_at', sa.DateTime(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('rotated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['default_suite_id'], ['testsuite.id']),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_incidentwebhook_project_id'), 'incidentwebhook', ['project_id'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_incidentwebhook_project_id'), table_name='incidentwebhook')
    op.drop_table('incidentwebhook')
    op.drop_index(op.f('ix_incident_status'), table_name='incident')
    op.drop_index(op.f('ix_incident_project_id'), table_name='incident')
    op.drop_index(op.f('ix_incident_fingerprint'), table_name='incident')
    op.drop_index(op.f('ix_incident_escaped_defect'), table_name='incident')
    op.drop_table('incident')
    op.drop_index(op.f('ix_requirement_tracker_config_id'), table_name='requirement')
    op.drop_index(op.f('ix_requirement_project_id'), table_name='requirement')
    op.drop_index(op.f('ix_requirement_external_key'), table_name='requirement')
    op.drop_table('requirement')
