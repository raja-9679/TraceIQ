import asyncio
import os
import sys

# Ensure we can import from app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import select
from app.core.database import get_session_context
from app.models import User, Tenant

async def debug_admin_permissions():
    print("Starting Admin Permission Debug...")
    
    async with get_session_context() as session:
        # 1. List Users
        print("\n[Step 1] Users:")
        users = await session.exec(select(User))
        all_users = users.all()
        for u in all_users:
            print(f" - ID: {u.id}, Email: {u.email}, Name: {u.full_name}")

        if not all_users:
            print("❌ No users found! Please register a user via Frontend first or run verification script.")
            return

        # 2. List Tenants
        print("\n[Step 2] Tenants:")
        tenants = await session.exec(select(Tenant))
        all_tenants = tenants.all()
        for t in all_tenants:
            print(f" - ID: {t.id}, Name: {t.name}, OwnerID: {t.owner_id}")

        # 3. Check/Fix Ownership
        print("\n[Step 3] Fixing Permissions...")
        
        # Strategy: Ensure the first user is a tenant owner
        target_user = all_users[0]
        
        # Check if they own a tenant
        user_tenant = next((t for t in all_tenants if t.owner_id == target_user.id), None)
        
        if user_tenant:
            print(f"✅ User {target_user.email} is already owner of Tenant '{user_tenant.name}'.")
        else:
            print(f"⚠️ User {target_user.email} is NOT a tenant owner. Creating Default Tenant...")
            new_tenant = Tenant(name="Default Tenant", owner_id=target_user.id)
            session.add(new_tenant)
            await session.commit()
            await session.refresh(new_tenant)
            print(f"✅ Created Tenant '{new_tenant.name}' owned by {target_user.email}. Retry the API call.")

if __name__ == "__main__":
    asyncio.run(debug_admin_permissions())
