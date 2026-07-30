"""users.is_instance_admin — explicit, transferable instance-operator grant

Managed via /api/admin/instance-admins and seeded by the ADMIN_EMAIL
bootstrap. Admins of the first tenant keep passing the guard as a fallback,
so no backfill is needed.

Revision ID: e7c2d3f4a5b6
Revises: d4a1b2c3e5f6
Create Date: 2026-07-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e7c2d3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'd4a1b2c3e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('is_instance_admin', sa.Boolean(),
                                     nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('users', 'is_instance_admin')
