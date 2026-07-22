"""executor keystone: multi-executor plumbing

Revision ID: d1e2f3a4b5c6
Revises: c2e3f4a5b6c7
Create Date: 2026-07-22 12:00:00.000000

Keystone for the multi-executor platform (PLATFORM_VISION.md §2):
- testcase.executor / testrun.executor — which worker runs a case/run.
  Stored as a plain string (not a native enum) so new executor types need no
  ALTER TYPE migration. Defaults to 'ui_playwright' (today's behaviour).
- testcaseresult.result_kind / result_payload — type-aware result payload so
  load (time-series) and security (findings) runs don't have to fit the
  step-oriented columns. NULL for the classic ui_playwright path.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, None] = 'c2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'testcase',
        sa.Column('executor', sa.String(), nullable=False,
                  server_default='ui_playwright'),
    )
    op.add_column(
        'testrun',
        sa.Column('executor', sa.String(), nullable=False,
                  server_default='ui_playwright'),
    )
    op.add_column(
        'testcaseresult',
        sa.Column('result_kind', sa.String(), nullable=True),
    )
    op.add_column(
        'testcaseresult',
        sa.Column('result_payload', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('testcaseresult', 'result_payload')
    op.drop_column('testcaseresult', 'result_kind')
    op.drop_column('testrun', 'executor')
    op.drop_column('testcase', 'executor')
