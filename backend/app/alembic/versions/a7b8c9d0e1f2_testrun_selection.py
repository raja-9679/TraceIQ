"""testrun selection_mode / selection_total (targeted runs)

Revision ID: a7b8c9d0e1f2
Revises: f2a3b4c5d6e7
Create Date: 2026-10-14 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('testrun', sa.Column('selection_mode', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('testrun', sa.Column('selection_total', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('testrun', 'selection_total')
    op.drop_column('testrun', 'selection_mode')
