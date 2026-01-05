import asyncio
from sqlmodel import select
from app.core.database import get_session_context
from app.models import Role, Permission, RolePermission

async def check_rbac():
    async with get_session_context() as session:
        print("--- ROLES ---")
        roles = await session.exec(select(Role))
        for r in roles.all():
            print(f"Role: {r.id} - {r.name}")
            
        print("\n--- PERMISSIONS ---")
        perms = await session.exec(select(Permission))
        for p in perms.all():
            print(f"Perm: {p.id} - {p.resource}:{p.action}")

        print("\n--- ROLE PERMISSIONS ---")
        rp = await session.exec(select(RolePermission))
        count = 0
        for x in rp.all():
            count += 1
        print(f"Total RolePermission links: {count}")

if __name__ == "__main__":
    asyncio.run(check_rbac())
