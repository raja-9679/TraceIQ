"""run lifecycle: cancelled / timed-out statuses, error kinds (WP15f)

teststatus gains CANCELLED and TIMED_OUT (enum labels are the uppercase
NAMES, like the rest of teststatus). ALTER TYPE ... ADD VALUE cannot run
inside a transaction block on older Postgres and its new value cannot be used
in the same transaction, so it runs in an autocommit block.

testcaseresult.error_kind: why a non-passing result did not pass.
testrun.cancelled_at: when a cancel was accepted.

Downgrade folds the two statuses back into ERROR and drops the columns; the
enum values themselves stay (Postgres cannot drop an enum value without
rebuilding the type, and an unused label is harmless). `downgrade base`
drops the whole type anyway.

Revision ID: d062b3c4d5fd
Revises: d052b3c4d5fc
Create Date: 2026-10-20 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'd062b3c4d5fd'
down_revision: Union[str, None] = 'd052b3c4d5fc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE teststatus ADD VALUE IF NOT EXISTS 'CANCELLED'")
        op.execute("ALTER TYPE teststatus ADD VALUE IF NOT EXISTS 'TIMED_OUT'")
    op.add_column('testcaseresult', sa.Column(
        'error_kind', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('testrun', sa.Column('cancelled_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('testrun', 'cancelled_at')
    op.drop_column('testcaseresult', 'error_kind')
    for table in ('testrun', 'testcaseresult'):
        op.execute(
            f"UPDATE {table} SET status = 'ERROR' "
            "WHERE status::text IN ('CANCELLED', 'TIMED_OUT')")
