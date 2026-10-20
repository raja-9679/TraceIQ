"""encrypt outbound webhook signing secrets at rest (WP15f)

workspacewebhook.secret held the HMAC signing secret in plaintext, unlike
every other stored credential. New rows are written as a `v1:` envelope
(app.core.secrets); this encrypts the existing ones. Readers accept both
(webhook_delivery.signing_secret), so the order of deploy and migration does
not matter. Downgrade decrypts them back for the older code.

Revision ID: d092b3c4d600
Revises: d082b3c4d5ff
Create Date: 2026-10-20 19:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd092b3c4d600'
down_revision: Union[str, None] = 'd082b3c4d5ff'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.core.secrets import encrypt_secret
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, secret FROM workspacewebhook WHERE secret NOT LIKE 'v1:%'")).fetchall()
    for row_id, secret in rows:
        conn.execute(sa.text("UPDATE workspacewebhook SET secret = :s WHERE id = :i"),
                     {"s": encrypt_secret(secret), "i": row_id})


def downgrade() -> None:
    from app.core.secrets import decrypt_secret
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, secret FROM workspacewebhook WHERE secret LIKE 'v1:%'")).fetchall()
    for row_id, secret in rows:
        conn.execute(sa.text("UPDATE workspacewebhook SET secret = :s WHERE id = :i"),
                     {"s": decrypt_secret(secret), "i": row_id})
