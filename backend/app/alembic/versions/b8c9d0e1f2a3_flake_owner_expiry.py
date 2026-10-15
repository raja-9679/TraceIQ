"""flakerecord owner + quarantine expiry/reason/source

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-10-15 12:00:00.000000

Rows already quarantined get a 14-day expiry from now, so nothing stays hidden
forever; their source stays NULL (legacy, treated as automatic).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('flakerecord', sa.Column('owner_user_id', sa.Integer(), nullable=True))
    op.add_column('flakerecord', sa.Column('quarantine_source', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('flakerecord', sa.Column('quarantine_reason', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('flakerecord', sa.Column('quarantined_at', sa.DateTime(), nullable=True))
    op.add_column('flakerecord', sa.Column('quarantined_by_id', sa.Integer(), nullable=True))
    op.add_column('flakerecord', sa.Column('quarantine_expires_at', sa.DateTime(), nullable=True))
    op.create_index(op.f('ix_flakerecord_owner_user_id'), 'flakerecord', ['owner_user_id'], unique=False)
    op.create_index(op.f('ix_flakerecord_quarantine_expires_at'), 'flakerecord', ['quarantine_expires_at'], unique=False)
    # Unnamed on purpose: the model's constraint names are generated, and a
    # named one here would show up as drift in `alembic check`.
    op.create_foreign_key(None, 'flakerecord', 'users', ['owner_user_id'], ['id'])
    op.create_foreign_key(None, 'flakerecord', 'users', ['quarantined_by_id'], ['id'])
    op.execute(
        "UPDATE flakerecord "
        "SET quarantine_expires_at = (now() AT TIME ZONE 'utc') + interval '14 days', "
        "    quarantined_at = last_observed_at "
        "WHERE is_quarantined")


def downgrade() -> None:
    op.drop_constraint('flakerecord_quarantined_by_id_fkey', 'flakerecord', type_='foreignkey')
    op.drop_constraint('flakerecord_owner_user_id_fkey', 'flakerecord', type_='foreignkey')
    op.drop_index(op.f('ix_flakerecord_quarantine_expires_at'), table_name='flakerecord')
    op.drop_index(op.f('ix_flakerecord_owner_user_id'), table_name='flakerecord')
    op.drop_column('flakerecord', 'quarantine_expires_at')
    op.drop_column('flakerecord', 'quarantined_by_id')
    op.drop_column('flakerecord', 'quarantined_at')
    op.drop_column('flakerecord', 'quarantine_reason')
    op.drop_column('flakerecord', 'quarantine_source')
    op.drop_column('flakerecord', 'owner_user_id')
