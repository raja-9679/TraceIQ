"""schedule timezone: cron expressions read in an IANA zone

Revision ID: cbb2b3c4d5ed
Revises: caa2b3c4d5eb
Create Date: 2026-10-16 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'cbb2b3c4d5ed'
down_revision: Union[str, None] = 'caa2b3c4d5eb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing schedules keep firing exactly as before: UTC.
    op.add_column('testschedule', sa.Column(
        'timezone', sqlmodel.sql.sqltypes.AutoString(), nullable=False,
        server_default='UTC'))


def downgrade() -> None:
    op.drop_column('testschedule', 'timezone')
