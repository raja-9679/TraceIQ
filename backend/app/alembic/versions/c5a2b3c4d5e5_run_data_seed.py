"""testrun.data_seed (effective {{fake.*}} seed recorded per run)

Revision ID: c5a2b3c4d5e5
Revises: c7a2b3c4d5e7
Create Date: 2026-10-15 16:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'c5a2b3c4d5e5'
down_revision: Union[str, None] = 'c7a2b3c4d5e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('testrun', sa.Column(
        'data_seed', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('testrun', 'data_seed')
