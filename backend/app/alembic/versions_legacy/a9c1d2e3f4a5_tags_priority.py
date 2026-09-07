"""tags_priority (merge dataset + phase-e heads)

Revision ID: a9c1d2e3f4a5
Revises: f8b3c4d5e6f7, c3d4e5f6a7b8
Create Date: 2026-07-17 12:00:00.000000

Adds test-management metadata to testcase: `tags` (JSON list) for
filtering/organising and tag-based run selection, plus a coarse `priority`
string. Also merges the two pre-existing migration heads (phase-e and the
dataset column branch) into a single head.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9c1d2e3f4a5'
down_revision: Union[str, Sequence[str], None] = ('f8b3c4d5e6f7', 'c3d4e5f6a7b8')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('testcase', sa.Column('tags', sa.JSON(), nullable=True))
    op.add_column('testcase', sa.Column('priority', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('testcase', 'priority')
    op.drop_column('testcase', 'tags')
