import asyncio
import sys
import os

# Create a new script to assign tenant admin role
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import select
from app.core.database import get_session_context
from app.models import User, Role, UserSystemRole, Tenant

async def assign_tenant_admin(email: str):
    async with get_session_context() as session:
        # 1. Find User
        user = (await session.exec(select(User).where(User.email == email))).first()
        if not user:
            print(f"Error: User with email '{email}' not found.")
            return

        # 2. Find Tenant Admin Role
        role = (await session.exec(select(Role).where(Role.name == "Tenant Admin"))).first()
        if not role:
            print("Error: 'Tenant Admin' role not found. Run setup_rbac.py first.")
            return

        # 3. Find Tenant (Assuming single tenant or first tenant for now)
        # In multi-tenant, we might need to specify tenant_id
        tenant = (await session.exec(select(Tenant))).first()
        if not tenant:
            print("Error: No tenants found.")
            return

        # 4. Check if already assigned
        existing = (await session.exec(select(UserSystemRole).where(
            UserSystemRole.user_id == user.id,
            UserSystemRole.role_id == role.id,
            UserSystemRole.tenant_id == tenant.id
        ))).first()
        
        if existing:
            print(f"User '{email}' is already a Tenant Admin for '{tenant.name}'.")
            return

        # 5. Assign Role
        usr = UserSystemRole(
            user_id=user.id,
            role_id=role.id,
            tenant_id=tenant.id
        )
        session.add(usr)
        await session.commit()
        print(f"Successfully assigned 'Tenant Admin' role to '{email}' for tenant '{tenant.name}'.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python assign_tenant_admin.py <email>")
        sys.exit(1)
    
    email = sys.argv[1]
    asyncio.run(assign_tenant_admin(email))
