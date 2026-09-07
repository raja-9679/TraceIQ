"""security scans (ZAP DAST)

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-07-22 18:00:00.000000

Active/authenticated DAST scans (PLATFORM_VISION.md P-4, item 6):
- securityscan table (one row per scan of a target).
- securityfinding.scan_id (nullable FK) so findings can come from a scan, and
  run_id becomes nullable (a finding attaches to a run OR a scan).
- project.security_settings (authorized-target allowlist + toggles).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7e8f9a0b1c2'
down_revision: Union[str, None] = 'c6d7e8f9a0b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'securityscan',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('target_url', sa.String(), nullable=False),
        sa.Column('scan_type', sa.String(), nullable=False, server_default='baseline'),
        sa.Column('authenticated', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('status', sa.String(), nullable=False, server_default='pending'),
        sa.Column('requested_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('counts', sa.JSON(), nullable=True),
        sa.Column('error', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['requested_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_securityscan_project_id', 'securityscan', ['project_id'])

    op.add_column('securityfinding', sa.Column('scan_id', sa.Integer(), nullable=True))
    op.create_index('ix_securityfinding_scan_id', 'securityfinding', ['scan_id'])
    op.create_foreign_key(
        'fk_securityfinding_scan_id', 'securityfinding', 'securityscan',
        ['scan_id'], ['id'], ondelete='CASCADE')
    op.alter_column('securityfinding', 'run_id', existing_type=sa.Integer(), nullable=True)

    op.add_column('project', sa.Column('security_settings', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('project', 'security_settings')
    op.alter_column('securityfinding', 'run_id', existing_type=sa.Integer(), nullable=False)
    op.drop_constraint('fk_securityfinding_scan_id', 'securityfinding', type_='foreignkey')
    op.drop_index('ix_securityfinding_scan_id', table_name='securityfinding')
    op.drop_column('securityfinding', 'scan_id')
    op.drop_index('ix_securityscan_project_id', table_name='securityscan')
    op.drop_table('securityscan')
