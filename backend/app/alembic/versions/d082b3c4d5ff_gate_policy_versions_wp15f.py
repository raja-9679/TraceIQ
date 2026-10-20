"""gate policy versions (WP15f L4)

gatepolicyversion: immutable, numbered snapshots of a project's effective
quality-gate policy (canonical JSON + SHA-256). Written on policy change and
lazily on first evaluation; every gate result names its version. No backfill:
the first evaluation after upgrade records version 1 from the policy then in
force.

Revision ID: d082b3c4d5ff
Revises: d072b3c4d5fe
Create Date: 2026-10-20 15:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'd082b3c4d5ff'
down_revision: Union[str, None] = 'd072b3c4d5fe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'gatepolicyversion',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('policy', sa.JSON(), nullable=False),
        sa.Column('sha256', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'version', name='uq_gatepolicyversion_project_version'),
    )
    op.create_index(op.f('ix_gatepolicyversion_project_id'), 'gatepolicyversion', ['project_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_gatepolicyversion_project_id'), table_name='gatepolicyversion')
    op.drop_table('gatepolicyversion')
