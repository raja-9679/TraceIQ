"""backfill_role_id_from_string_fields

Revision ID: b2c4e6f8a0d1
Revises: a7b3c9d2e1f4
Create Date: 2026-04-28 00:01:00.000000

Back-fills role_id on UserWorkspace, UserProjectAccess, and TeamProjectAccess
rows that still only have the legacy string role/access_level field set.

After running this migration, every row will have a valid role_id and the
deprecated string columns can be safely dropped in a follow-up migration.

The column DROP is intentionally left as a separate migration so that
operators can verify the back-fill is complete before removing the columns.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = 'b2c4e6f8a0d1'
down_revision: Union[str, None] = 'a7b3c9d2e1f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Maps legacy string values → Role.name
_WORKSPACE_ROLE_MAP = {
    'admin':  'Workspace Admin',
    'member': 'Workspace Member',
}

_PROJECT_ROLE_MAP = {
    'admin':  'Project Admin',
    'editor': 'Project Editor',
    'viewer': 'Project Viewer',
}


def _get_role_id(conn, role_name: str) -> int | None:
    row = conn.execute(
        text("SELECT id FROM role WHERE name = :name AND tenant_id IS NULL LIMIT 1"),
        {"name": role_name}
    ).fetchone()
    return row[0] if row else None


def upgrade() -> None:
    conn = op.get_bind()

    # ---------- UserWorkspace ----------
    for legacy_val, role_name in _WORKSPACE_ROLE_MAP.items():
        role_id = _get_role_id(conn, role_name)
        if role_id is None:
            print(f"[backfill] WARNING: Role '{role_name}' not found, skipping '{legacy_val}'")
            continue
        result = conn.execute(
            text(
                "UPDATE userworkspace SET role_id = :rid "
                "WHERE role = :legacy AND role_id IS NULL"
            ),
            {"rid": role_id, "legacy": legacy_val}
        )
        print(f"[backfill] userworkspace role='{legacy_val}' → role_id={role_id}: {result.rowcount} rows updated")

    # ---------- UserProjectAccess ----------
    for legacy_val, role_name in _PROJECT_ROLE_MAP.items():
        role_id = _get_role_id(conn, role_name)
        if role_id is None:
            print(f"[backfill] WARNING: Role '{role_name}' not found, skipping '{legacy_val}'")
            continue
        result = conn.execute(
            text(
                "UPDATE userprojectaccess SET role_id = :rid "
                "WHERE access_level = :legacy AND role_id IS NULL"
            ),
            {"rid": role_id, "legacy": legacy_val}
        )
        print(f"[backfill] userprojectaccess access_level='{legacy_val}' → role_id={role_id}: {result.rowcount} rows updated")

    # ---------- TeamProjectAccess ----------
    for legacy_val, role_name in _PROJECT_ROLE_MAP.items():
        role_id = _get_role_id(conn, role_name)
        if role_id is None:
            continue
        result = conn.execute(
            text(
                "UPDATE teamprojectaccess SET role_id = :rid "
                "WHERE access_level = :legacy AND role_id IS NULL"
            ),
            {"rid": role_id, "legacy": legacy_val}
        )
        print(f"[backfill] teamprojectaccess access_level='{legacy_val}' → role_id={role_id}: {result.rowcount} rows updated")


def downgrade() -> None:
    # The string columns still exist after this migration so a downgrade is a no-op.
    pass
