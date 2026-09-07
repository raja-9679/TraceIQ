"""Per-test-case browser/device run matrix override

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-07-27

`run_matrix` holds {"browsers": [...], "devices": [...]}. NULL means the
case inherits the suite chain's execution matrix (settings inheritance).
"""
from alembic import op
import sqlalchemy as sa

revision = "f7a8b9c0d1e2"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("testcase", sa.Column("run_matrix", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("testcase", "run_matrix")
