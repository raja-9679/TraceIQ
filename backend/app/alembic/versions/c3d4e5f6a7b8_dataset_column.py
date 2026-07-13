"""dataset_column

Revision ID: c3d4e5f6a7b8
Revises: b7d8e9f0a1b2
Create Date: 2026-07-13 16:00:00.000000

Data-driven tests: testcase.dataset holds a list of row objects; the case
expands into one execution per row at dispatch ({{data.KEY}} in steps).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b7d8e9f0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('testcase', sa.Column('dataset', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('testcase', 'dataset')
