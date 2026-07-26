"""Workspace auto-apply threshold for agent proposals

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-07-26 16:00:00.000000

Proposals with ai_confidence >= workspace.auto_apply_threshold auto-merge
(CREATE/UPDATE only). NULL = disabled. Enabled by the TestCaseRevision
safety net shipped in c4d5e6f7a8b9.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, None] = 'c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('workspace', sa.Column('auto_apply_threshold', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('workspace', 'auto_apply_threshold')
