"""instance_settings table (admin-editable DB-over-env config) + merge heads

The chain had drifted into three heads (finalized_at, dataset_column,
requirement_links); this revision merges them and creates the table backing
app/services/instance_settings.py.

Revision ID: cb390bfd3836
Revises: a2f4c6d8e0b1, c3d4e5f6a7b8, e4f5a6b7c8d9
Create Date: 2026-07-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'cb390bfd3836'
down_revision: Union[str, Sequence[str], None] = (
    'a2f4c6d8e0b1', 'c3d4e5f6a7b8', 'e4f5a6b7c8d9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'instance_settings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('value', sa.String(), nullable=False),
        sa.Column('is_secret', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('updated_by_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('now()')),
    )
    op.create_index('ix_instance_settings_key', 'instance_settings', ['key'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_instance_settings_key', table_name='instance_settings')
    op.drop_table('instance_settings')
