"""webhookcapture: requests captured for wait-for-webhook steps

Revision ID: c2a2b3c4d5e2
Revises: c3a2b3c4d5e3
Create Date: 2026-10-15 15:00:00.000000

Short-lived rows (swept after 24 h); the inbox side of the feature stores
nothing — addresses are derived and mail stays with the provider.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'c2a2b3c4d5e2'
down_revision: Union[str, None] = 'c3a2b3c4d5e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'webhookcapture',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('token', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=False),
        sa.Column('test_case_id', sa.Integer(), nullable=False),
        sa.Column('label', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('method', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('path', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('query', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('headers', sa.JSON(), nullable=True),
        sa.Column('content_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('body', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('body_size', sa.Integer(), nullable=False),
        sa.Column('truncated', sa.Boolean(), nullable=False),
        sa.Column('received_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['run_id'], ['testrun.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_webhookcapture_token'), 'webhookcapture', ['token'], unique=False)
    op.create_index(op.f('ix_webhookcapture_run_id'), 'webhookcapture', ['run_id'], unique=False)
    op.create_index(op.f('ix_webhookcapture_received_at'), 'webhookcapture', ['received_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_webhookcapture_received_at'), table_name='webhookcapture')
    op.drop_index(op.f('ix_webhookcapture_run_id'), table_name='webhookcapture')
    op.drop_index(op.f('ix_webhookcapture_token'), table_name='webhookcapture')
    op.drop_table('webhookcapture')
