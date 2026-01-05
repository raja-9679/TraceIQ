import asyncio
import sys
import os
from sqlmodel import select

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import get_session_context
from app.models import User, Tenant, UserSystemRole, Organization, Role, UserOrganization
from app.api.auth import UserCreate, register_user
from app.services.org_service import org_service

async def verify_multi_tenant_admin():
    print("Verifying Multiple Tenant Admins Capability...")
    async with get_session_context() as session:
        # 1. Setup: Create Tenant Owner (Admin 1)
        print("\n[Step 1] Creating Owner (Admin 1)")
        owner_email = "owner@test.com"
        owner_in = UserCreate(email=owner_email, password="password", full_name="Owner")
        try:
            owner = await register_user(owner_in, session)
        except Exception:
             # Fetch if exists
             owner = (await session.exec(select(User).where(User.email == owner_email))).first()

        # Get Tenant
        tenant = (await session.exec(select(Tenant).where(Tenant.owner_id == owner.id))).first()
        print(f"  - Tenant: {tenant.name} (ID: {tenant.id})")

        # Get Default Org
        orgs = await org_service.get_user_organizations(owner.id, session)
        target_org = next((o for o in orgs if o.tenant_id == tenant.id), None)
        print(f"  - Target Org: {target_org.name} (ID: {target_org.id})")

        # 2. Assign Secondary Admin
        print("\n[Step 2] Creating and Assigning Secondary Admin (Admin 2)")
        admin2_email = "admin2@test.com"
        # Create user manually or via register (register will create NEW tenant, we don't want that for Admin 2 ideally, 
        # but register_user logic forces it if no token. 
        # So let's create user manually to avoid creating a second tenant unnecessarily, 
        # OR use register and ignore their own tenant.)
        
        # Use manual creation to simulate "Added to system"
        from app.core.auth import get_password_hash
        admin2 = (await session.exec(select(User).where(User.email == admin2_email))).first()
        if not admin2:
            admin2 = User(email=admin2_email, hashed_password=get_password_hash("password"), full_name="Admin Two")
            session.add(admin2)
            await session.commit()
            await session.refresh(admin2)
        
        # Make Admin 2 a Tenant Admin of `tenant`
        ta_role = (await session.exec(select(Role).where(Role.name == "Tenant Admin"))).first()
        
        # Check existing role
        usr = (await session.exec(select(UserSystemRole).where(
            UserSystemRole.user_id == admin2.id,
            UserSystemRole.tenant_id == tenant.id
        ))).first()
        
        if not usr:
            usr = UserSystemRole(user_id=admin2.id, role_id=ta_role.id, tenant_id=tenant.id)
            session.add(usr)
            await session.commit()
            print("  - Assigned 'Tenant Admin' role to Admin 2")
        else:
            print("  - Admin 2 is already Tenant Admin")

        # 3. Create Victim User (to be assigned)
        print("\n[Step 3] Creating 'Victim' User")
        victim_email = "victim@test.com"
        victim = (await session.exec(select(User).where(User.email == victim_email))).first()
        if not victim:
            victim = User(email=victim_email, hashed_password=get_password_hash("password"), full_name="Victim")
            session.add(victim)
            await session.commit()
            await session.refresh(victim)

        # 4. Admin 2 performs Assignment (Simulating API Logic)
        print("\n[Step 4] Admin 2 works to assign Victim to Target Org")
        # Logic from api/admin.py: assign_user_to_orgs
        
        # A. Check Permissions (Mocking Dependency)
        # rbac_service.has_permission would pass because Admin 2 has Role
        
        # B. Check Scope
        stmt = select(UserSystemRole.tenant_id).where(UserSystemRole.user_id == admin2.id)
        admin_tenant_ids = (await session.exec(stmt)).all()
        print(f"  - Admin 2 Tenants: {admin_tenant_ids}")
        
        if target_org.tenant_id not in admin_tenant_ids:
            print("  ❌ FATAL: Logic failed, Admin 2 does not control Org's Tenant")
            return

        # C. Perform Assignment
        print("  - Scope Validated. Proceeding with Invite/Add...")
        # Use invite_user_to_organization directly as the API does
        # Note: API calls `invite_user_to_organization(..., invited_by_id=current_user.id)`
        
        await org_service.invite_user_to_organization(
            email=victim.email,
            org_id=target_org.id,
            invited_by_id=admin2.id,
            role="member",
            session=session
        )
        # Since victim exists, they should be added directly (or invited if separate logic, check service)
        # org_service line 296: "if user: ... session.add(uo)"
        # So they should be added directly.
        
        # 5. Verify Result
        uo = (await session.exec(select(UserOrganization).where(
            UserOrganization.user_id == victim.id,
            UserOrganization.organization_id == target_org.id
        ))).first()
        
        if uo:
            print("  ✅ SUCCESS: Victim successfully added to Org by Admin 2.")
        else:
            print("  ❌ FAILURE: Victim NOT added to Org.")

if __name__ == "__main__":
    asyncio.run(verify_multi_tenant_admin())
