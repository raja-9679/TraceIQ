"""Backfill legacy RBAC rows onto the current role model. Idempotent.

Replaces `setup_rbac.py`, which is deleted. That script seeded its own
permission vocabulary — `org:manage_users`, `org:create_project`,
`org:delete_org` — while every live permission check asks for
`workspace:`/`project:`/`test:` scopes (see `app/core/rbac_init.py`). Running it
produced plausible-looking roles that granted nothing, and two error messages in
the codebase told operators to run it when a role was missing. Role and
permission seeding now has exactly one owner: `init_rbac`, called from the app's
lifespan hook and by `scripts/bootstrap_db.py`.

What genuinely needed keeping are the two backfills, for databases that predate
`role_id` columns:

  1. `UserProjectAccess.role_id` from the legacy `access_level` string.
  2. `UserSystemRole` (Tenant Admin) for tenant owners who have no system role —
     e.g. tenants created before the role existed.

Both are safe to re-run: they only fill in what is missing and never downgrade
an existing grant.

    python scripts/backfill_rbac.py [--dry-run]
"""
import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import select  # noqa: E402

from app.core.database import get_session_context  # noqa: E402
from app.models import (  # noqa: E402
    Role, TeamProjectAccess, Tenant, UserProjectAccess, UserSystemRole,
)

# Legacy access_level string -> current system role name.
LEVEL_TO_ROLE = {"admin": "Project Admin", "editor": "Project Editor",
                 "viewer": "Project Viewer"}


async def backfill(dry_run: bool = False) -> None:
    async with get_session_context() as session:
        roles = {r.name: r for r in (await session.exec(
            select(Role).where(Role.tenant_id == None))).all()}  # noqa: E711
        missing = [name for name in set(LEVEL_TO_ROLE.values()) | {"Tenant Admin"}
                   if name not in roles]
        if missing:
            print(f"ERROR: system roles missing: {', '.join(sorted(missing))}")
            print("       Start the backend once (its lifespan hook runs init_rbac),")
            print("       or run scripts/bootstrap_db.py, then re-run this.")
            return

        # 1. UserProjectAccess.role_id
        rows = (await session.exec(select(UserProjectAccess).where(
            UserProjectAccess.role_id == None))).all()  # noqa: E711
        filled = 0
        for row in rows:
            role_name = LEVEL_TO_ROLE.get((row.access_level or "").lower())
            if not role_name:
                print(f"  ? UserProjectAccess(user={row.user_id}, "
                      f"project={row.project_id}) has access_level "
                      f"{row.access_level!r} — left alone")
                continue
            row.role_id = roles[role_name].id
            session.add(row)
            filled += 1
        print(f"[1/3] UserProjectAccess: {filled} of {len(rows)} unmapped rows filled")

        # 2. TeamProjectAccess.role_id — same legacy column, same mapping.
        team_rows = (await session.exec(select(TeamProjectAccess).where(
            TeamProjectAccess.role_id == None))).all()  # noqa: E711
        team_filled = 0
        for row in team_rows:
            role_name = LEVEL_TO_ROLE.get((row.access_level or "").lower())
            if not role_name:
                continue
            row.role_id = roles[role_name].id
            session.add(row)
            team_filled += 1
        print(f"[2/3] TeamProjectAccess: {team_filled} of {len(team_rows)} "
              "unmapped rows filled")

        # 3. Tenant owners without a Tenant Admin system role.
        tenant_admin = roles["Tenant Admin"]
        granted = 0
        for tenant in (await session.exec(select(Tenant))).all():
            existing = (await session.exec(select(UserSystemRole).where(
                UserSystemRole.user_id == tenant.owner_id,
                UserSystemRole.tenant_id == tenant.id))).first()
            if existing:
                continue
            session.add(UserSystemRole(user_id=tenant.owner_id,
                                       role_id=tenant_admin.id,
                                       tenant_id=tenant.id))
            granted += 1
            print(f"  + Tenant Admin -> user {tenant.owner_id} for tenant "
                  f"{tenant.id} ({tenant.name})")
        print(f"[3/3] Tenant owners: {granted} system roles granted")

        if dry_run:
            await session.rollback()
            print("\nDry run — nothing committed.")
            return
        await session.commit()
        print("\nBackfill complete.")


if __name__ == "__main__":
    asyncio.run(backfill(dry_run="--dry-run" in sys.argv))
