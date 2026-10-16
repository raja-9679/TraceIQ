"""selector-heal candidates, snapshot reference and recheck (WP14c / W5)

selectorhealproposal.candidates: the ranked candidates with their evidence.
chosen_selector: the candidate applied (the system's pick until a reviewer
accepts another). dom_snapshot_key: object-store key of the redacted DOM the
candidates were scored against (the snapshot itself is not in the row).
recheck: the last accept-time uniqueness recheck against that snapshot.

Revision ID: d032b3c4d5fa
Revises: d022b3c4d5f9
Create Date: 2026-10-16 23:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'd032b3c4d5fa'
down_revision: Union[str, None] = 'd022b3c4d5f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('selectorhealproposal', sa.Column('candidates', sa.JSON(), nullable=True))
    op.add_column('selectorhealproposal', sa.Column(
        'chosen_selector', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('selectorhealproposal', sa.Column(
        'dom_snapshot_key', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('selectorhealproposal', sa.Column('recheck', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('selectorhealproposal', 'recheck')
    op.drop_column('selectorhealproposal', 'dom_snapshot_key')
    op.drop_column('selectorhealproposal', 'chosen_selector')
    op.drop_column('selectorhealproposal', 'candidates')
