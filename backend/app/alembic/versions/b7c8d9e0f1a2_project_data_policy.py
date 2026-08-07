"""Add Project.data_policy (capture + redaction policy)

Nullable by design, following the same convention as quality_gate_policy,
ci_settings and security_settings: NULL means "use the built-in default",
which for this column is capture_level 'standard'.

That default is a deliberate behaviour change for existing projects. Before
this column existed every run captured everything — video, Playwright traces,
optionally a HAR, plus request and response bodies stored verbatim. After this
migration an existing project keeps capturing masked screenshots and scrubbed
logs, but stops recording video, traces and HAR until someone opts that project
back up to 'full'. Backfilling every row to 'full' to preserve the old
behaviour was the alternative and was rejected: silently carrying forward the
most revealing setting is exactly the failure mode this work exists to remove.

Revision ID: b7c8d9e0f1a2
Revises: a3b4c5d6e7f8
"""
from alembic import op
import sqlalchemy as sa


revision = 'b7c8d9e0f1a2'
down_revision = 'a3b4c5d6e7f8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('project') as batch:
        batch.add_column(sa.Column('data_policy', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('project') as batch:
        batch.drop_column('data_policy')
