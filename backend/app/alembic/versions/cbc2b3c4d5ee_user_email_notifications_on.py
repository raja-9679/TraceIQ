"""user_settings.email_notifications: default on, existing rows on

The switch was saved but never read until 2026-10: every run owner was
emailed regardless. It now decides whether the owner is emailed, so stored
`false` values (mostly untouched defaults) would silently stop emails people
currently get. Existing rows are set to what they effectively had (on), and
the column default becomes on.

Revision ID: cbc2b3c4d5ee
Revises: cbb2b3c4d5ed
Create Date: 2026-10-16 15:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'cbc2b3c4d5ee'
down_revision: Union[str, None] = 'cbb2b3c4d5ed'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE user_settings SET email_notifications = true")
    op.alter_column('user_settings', 'email_notifications',
                    existing_type=sa.Boolean(), server_default=sa.true())


def downgrade() -> None:
    # Values are not restored: the old ones were never read, so "on" loses
    # nothing a user had.
    op.alter_column('user_settings', 'email_notifications',
                    existing_type=sa.Boolean(), server_default=None)
