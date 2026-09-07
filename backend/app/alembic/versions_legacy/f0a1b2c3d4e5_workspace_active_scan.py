"""Workspace-level active-scan toggle

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
Create Date: 2026-07-23 17:30:00.000000

A workspace admin can enable active (attacking) DAST scans from the UI,
replacing the need to set SECURITY_ACTIVE_SCAN_ENABLED via env + container
recreate. The env flag remains a global override.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f0a1b2c3d4e5'
down_revision: Union[str, None] = 'e9f0a1b2c3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('workspace', sa.Column('active_scan_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('workspace', 'active_scan_enabled')
