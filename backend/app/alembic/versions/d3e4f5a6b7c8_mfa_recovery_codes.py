"""mfa recovery codes

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-07-22 23:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd3e4f5a6b7c8'
down_revision: Union[str, None] = 'c2d3e4f5a6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'mfarecoverycode',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('code_hash', sa.String(), nullable=False),
        sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_mfarecoverycode_user_id', 'mfarecoverycode', ['user_id'])
    op.create_index('ix_mfarecoverycode_code_hash', 'mfarecoverycode', ['code_hash'])


def downgrade() -> None:
    op.drop_index('ix_mfarecoverycode_code_hash', table_name='mfarecoverycode')
    op.drop_index('ix_mfarecoverycode_user_id', table_name='mfarecoverycode')
    op.drop_table('mfarecoverycode')
