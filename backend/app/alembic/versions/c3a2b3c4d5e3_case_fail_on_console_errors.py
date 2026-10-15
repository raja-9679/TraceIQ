"""testcase.fail_on_console_errors (per-case override, NULL = inherit)

Revision ID: c3a2b3c4d5e3
Revises: b8c9d0e1f2a3
Create Date: 2026-10-15 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c3a2b3c4d5e3'
down_revision: Union[str, None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('testcase', sa.Column('fail_on_console_errors', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('testcase', 'fail_on_console_errors')
