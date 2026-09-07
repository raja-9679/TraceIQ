"""Add testrun.finalized_at as the finalize idempotency marker

Revision ID: a2f4c6d8e0b1
Revises: f7a8b9c0d1e2
Create Date: 2026-07-28

POST /api/runs/{id}/finalize dispatches six side-effecting tasks (notifications,
outbound webhooks, heal proposals, monitor evaluation, passive security scan,
failure clustering). Workers retry finalize on network failure, and it had no
guard, so a redelivery re-sent customer emails and re-fired outbound webhooks
into customer CI. `finalized_at` is claimed with a conditional UPDATE so a
concurrent retry loses the race.

Existing runs already in a terminal state are backfilled to their created_at so
they are not re-finalized if a stale worker retries after deploy. Runs still in
flight stay NULL and finalize normally.
"""
from alembic import op
import sqlalchemy as sa

revision = "a2f4c6d8e0b1"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("testrun", sa.Column("finalized_at", sa.DateTime(), nullable=True))
    # Treat already-completed runs as finalized. Without this, a worker holding
    # a queued retry from before the deploy would fire a duplicate notification
    # for a run the user already saw.
    # Cast to text and upper-case rather than comparing against enum literals
    # directly: `status` is a native Postgres enum on this table (storing
    # SQLAlchemy's uppercase member *names*) but a plain String elsewhere in the
    # schema, and this way the backfill is correct under either representation.
    op.execute(
        """
        UPDATE testrun
           SET finalized_at = created_at
         WHERE finalized_at IS NULL
           AND UPPER(status::text) IN ('PASSED', 'FAILED', 'ERROR')
        """
    )
    op.create_index(
        "ix_testrun_finalized_at", "testrun", ["finalized_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_testrun_finalized_at", table_name="testrun")
    op.drop_column("testrun", "finalized_at")
