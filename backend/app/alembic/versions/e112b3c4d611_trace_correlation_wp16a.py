"""trace correlation with the application under test (WP16a)

Adds project.trace_settings (JSON, NULL = off): when enabled, workers add a
W3C traceparent to requests to the project's own hosts and record the ids in
step evidence; the backend reads the matching spans from a configured
Jaeger / Tempo query API.

Revision ID: e112b3c4d611
Revises: d092b3c4d600
Create Date: 2026-10-20 21:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e112b3c4d611'
down_revision: Union[str, None] = 'd092b3c4d600'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('project', sa.Column('trace_settings', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('project', 'trace_settings')
