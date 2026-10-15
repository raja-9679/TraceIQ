"""security operations: SIEM outbox, finding workflow, access reviews (WP7)

Revision ID: c7a2b3c4d5e7
Revises: b8c9d0e1f2a3
Create Date: 2026-10-15 18:00:00.000000

Finding statuses are renamed to the WP7 workflow vocabulary:
acknowledged → in_progress, resolved → verified_fixed. Downgrade folds the
new states back onto the old four.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'c7a2b3c4d5e7'
down_revision: Union[str, None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('securityeventoutbox',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('audit_row_hash', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('payload', sa.JSON(), nullable=True),
    sa.Column('needs_delivery', sa.Boolean(), nullable=False),
    sa.Column('needs_alert_eval', sa.Boolean(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(), nullable=False),
    sa.Column('last_error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_securityeventoutbox_next_attempt_at'), 'securityeventoutbox', ['next_attempt_at'], unique=False)

    op.create_table('accessreviewcampaign',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('workspace_id', sa.Integer(), nullable=True),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('signed_off_by_id', sa.Integer(), nullable=True),
    sa.Column('signed_off_at', sa.DateTime(), nullable=True),
    sa.Column('sign_off_note', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('revocations_applied_at', sa.DateTime(), nullable=True),
    sa.Column('revocations_applied_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['revocations_applied_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['signed_off_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_accessreviewcampaign_workspace_id'), 'accessreviewcampaign', ['workspace_id'], unique=False)

    op.create_table('accessreviewitem',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('campaign_id', sa.Integer(), nullable=False),
    sa.Column('subject_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('subject_id', sa.Integer(), nullable=False),
    sa.Column('label', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('snapshot', sa.JSON(), nullable=True),
    sa.Column('decision', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('note', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('decided_by_id', sa.Integer(), nullable=True),
    sa.Column('decided_at', sa.DateTime(), nullable=True),
    sa.Column('applied_at', sa.DateTime(), nullable=True),
    sa.Column('apply_result', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.ForeignKeyConstraint(['campaign_id'], ['accessreviewcampaign.id'], ),
    sa.ForeignKeyConstraint(['decided_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_accessreviewitem_campaign_id'), 'accessreviewitem', ['campaign_id'], unique=False)

    op.add_column('workspace', sa.Column('finding_sla_days', sa.JSON(), nullable=True))

    op.add_column('securityfinding', sa.Column('due_at', sa.DateTime(), nullable=True))
    op.add_column('securityfinding', sa.Column('risk_justification', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('securityfinding', sa.Column('risk_expires_at', sa.DateTime(), nullable=True))
    op.add_column('securityfinding', sa.Column('risk_approved_by_id', sa.Integer(), nullable=True))
    op.add_column('securityfinding', sa.Column('risk_approved_at', sa.DateTime(), nullable=True))
    op.add_column('securityfinding', sa.Column('verified_at', sa.DateTime(), nullable=True))
    op.add_column('securityfinding', sa.Column('superseded_by_id', sa.Integer(), nullable=True))
    op.add_column('securityfinding', sa.Column('retest_scan_id', sa.Integer(), nullable=True))
    op.add_column('securityfinding', sa.Column('location', sa.JSON(), nullable=True))
    op.create_index(op.f('ix_securityfinding_due_at'), 'securityfinding', ['due_at'], unique=False)
    op.create_index(op.f('ix_securityfinding_risk_expires_at'), 'securityfinding', ['risk_expires_at'], unique=False)
    op.create_index(op.f('ix_securityfinding_superseded_by_id'), 'securityfinding', ['superseded_by_id'], unique=False)
    # Unnamed on purpose: a named constraint would show up as drift in
    # `alembic check` against the model's generated name.
    op.create_foreign_key(None, 'securityfinding', 'users', ['risk_approved_by_id'], ['id'])

    op.execute("UPDATE securityfinding SET status = 'in_progress' WHERE status = 'acknowledged'")
    op.execute("UPDATE securityfinding SET status = 'verified_fixed' WHERE status = 'resolved'")


def downgrade() -> None:
    op.execute("UPDATE securityfinding SET status = 'acknowledged' "
               "WHERE status IN ('in_progress', 'fixed_pending_verification', 'accepted_risk')")
    op.execute("UPDATE securityfinding SET status = 'resolved' WHERE status = 'verified_fixed'")

    op.drop_constraint('securityfinding_risk_approved_by_id_fkey', 'securityfinding', type_='foreignkey')
    op.drop_index(op.f('ix_securityfinding_superseded_by_id'), table_name='securityfinding')
    op.drop_index(op.f('ix_securityfinding_risk_expires_at'), table_name='securityfinding')
    op.drop_index(op.f('ix_securityfinding_due_at'), table_name='securityfinding')
    op.drop_column('securityfinding', 'location')
    op.drop_column('securityfinding', 'retest_scan_id')
    op.drop_column('securityfinding', 'superseded_by_id')
    op.drop_column('securityfinding', 'verified_at')
    op.drop_column('securityfinding', 'risk_approved_at')
    op.drop_column('securityfinding', 'risk_approved_by_id')
    op.drop_column('securityfinding', 'risk_expires_at')
    op.drop_column('securityfinding', 'risk_justification')
    op.drop_column('securityfinding', 'due_at')

    op.drop_column('workspace', 'finding_sla_days')

    op.drop_index(op.f('ix_accessreviewitem_campaign_id'), table_name='accessreviewitem')
    op.drop_table('accessreviewitem')
    op.drop_index(op.f('ix_accessreviewcampaign_workspace_id'), table_name='accessreviewcampaign')
    op.drop_table('accessreviewcampaign')
    op.drop_index(op.f('ix_securityeventoutbox_next_attempt_at'), table_name='securityeventoutbox')
    op.drop_table('securityeventoutbox')
