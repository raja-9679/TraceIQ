"""raw_playwright: testcase.raw_script

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-07-22 17:00:00.000000

Store an uploaded Playwright spec on a raw_playwright test case
(PLATFORM_VISION.md §4). NULL for step-based (ui_playwright) cases.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c6d7e8f9a0b1'
down_revision: Union[str, None] = 'b5c6d7e8f9a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('testcase', sa.Column('raw_script', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('testcase', 'raw_script')
