"""add_performance_indexes

Revision ID: a7b3c9d2e1f4
Revises: 1f266105057e
Create Date: 2026-04-28 00:00:00.000000

Adds indexes on the highest-frequency query columns.
All indexes are created CONCURRENTLY so they do not lock tables in production.
CONCURRENTLY is not supported inside a transaction, so each statement is
executed with op.execute() outside of the default migration transaction.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7b3c9d2e1f4'
down_revision: Union[str, None] = '1f266105057e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # CONCURRENTLY cannot run inside a transaction block.
    op.get_bind().execute(sa.text('COMMIT'))

    op.get_bind().execute(sa.text(
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_testrun_created_at '
        'ON testrun (created_at DESC)'
    ))
    op.get_bind().execute(sa.text(
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_testrun_project_id '
        'ON testrun (project_id)'
    ))
    op.get_bind().execute(sa.text(
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_testrun_status '
        'ON testrun (status)'
    ))
    op.get_bind().execute(sa.text(
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_testcase_test_suite_id '
        'ON testcase (test_suite_id)'
    ))
    op.get_bind().execute(sa.text(
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_testcaseresult_test_run_id '
        'ON testcaseresult (test_run_id)'
    ))
    op.get_bind().execute(sa.text(
        'CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_user_email '
        'ON "user" (email)'
    ))


def downgrade() -> None:
    op.get_bind().execute(sa.text('COMMIT'))

    op.get_bind().execute(sa.text('DROP INDEX CONCURRENTLY IF EXISTS ix_testrun_created_at'))
    op.get_bind().execute(sa.text('DROP INDEX CONCURRENTLY IF EXISTS ix_testrun_project_id'))
    op.get_bind().execute(sa.text('DROP INDEX CONCURRENTLY IF EXISTS ix_testrun_status'))
    op.get_bind().execute(sa.text('DROP INDEX CONCURRENTLY IF EXISTS ix_testcase_test_suite_id'))
    op.get_bind().execute(sa.text('DROP INDEX CONCURRENTLY IF EXISTS ix_testcaseresult_test_run_id'))
    op.get_bind().execute(sa.text('DROP INDEX CONCURRENTLY IF EXISTS ix_user_email'))
