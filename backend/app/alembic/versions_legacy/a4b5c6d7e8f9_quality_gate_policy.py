"""quality gate policy on project

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-07-22 15:00:00.000000

Per-project release-gate thresholds (PLATFORM_VISION.md §5). Nullable JSON;
NULL means "use the built-in DEFAULT_QUALITY_GATE".
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4b5c6d7e8f9'
down_revision: Union[str, None] = 'f3a4b5c6d7e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('project', sa.Column('quality_gate_policy', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('project', 'quality_gate_policy')
