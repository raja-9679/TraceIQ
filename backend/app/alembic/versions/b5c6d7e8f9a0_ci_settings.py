"""ci settings on project

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-07-22 16:00:00.000000

Per-project CI/PR-reporting config (PLATFORM_VISION.md §5, item 4). Nullable
JSON; NULL means the built-in DEFAULT_CI_SETTINGS (disabled, opt-in).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5c6d7e8f9a0'
down_revision: Union[str, None] = 'a4b5c6d7e8f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('project', sa.Column('ci_settings', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('project', 'ci_settings')
