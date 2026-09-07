"""Security finding lifecycle + scheduled security scans

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-07-23 15:00:00.000000

Findings gain triage state (status/assignee/resolved_at) and a cross-scan
fingerprint (drives scan diffs + false-positive carry-forward). TestSchedule
gains security_scan_target for recurring baseline scans.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8e9f0a1b2c3'
down_revision: Union[str, None] = 'c7d8e9f0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('securityfinding', sa.Column('status', sa.String(), nullable=False, server_default='open'))
    op.add_column('securityfinding', sa.Column('assignee_id', sa.Integer(), nullable=True))
    op.add_column('securityfinding', sa.Column('resolved_at', sa.DateTime(), nullable=True))
    op.add_column('securityfinding', sa.Column('fingerprint', sa.String(), nullable=True))
    op.create_index('ix_securityfinding_status', 'securityfinding', ['status'])
    op.create_index('ix_securityfinding_fingerprint', 'securityfinding', ['fingerprint'])
    op.create_foreign_key('fk_securityfinding_assignee', 'securityfinding', 'users', ['assignee_id'], ['id'])
    op.add_column('testschedule', sa.Column('security_scan_target', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('testschedule', 'security_scan_target')
    op.drop_constraint('fk_securityfinding_assignee', 'securityfinding', type_='foreignkey')
    op.drop_index('ix_securityfinding_fingerprint', table_name='securityfinding')
    op.drop_index('ix_securityfinding_status', table_name='securityfinding')
    op.drop_column('securityfinding', 'fingerprint')
    op.drop_column('securityfinding', 'resolved_at')
    op.drop_column('securityfinding', 'assignee_id')
    op.drop_column('securityfinding', 'status')
