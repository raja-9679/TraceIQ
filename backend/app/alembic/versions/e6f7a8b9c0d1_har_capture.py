"""HAR network-archive artifacts

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-07-26 18:00:00.000000

har_url on testrun + testcaseresult: opt-in HAR capture (suite settings
`har_capture: true` or HAR_CAPTURE_ENABLED on the worker) uploads a .har
per job alongside video/trace.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6f7a8b9c0d1'
down_revision: Union[str, None] = 'd5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('testrun', sa.Column('har_url', sa.String(), nullable=True))
    op.add_column('testcaseresult', sa.Column('har_url', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('testcaseresult', 'har_url')
    op.drop_column('testrun', 'har_url')
