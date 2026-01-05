import asyncio
import sys
import os

# Add backend directory
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.database import get_session
from app.services.rbac_service import rbac_service
from app.models import User, Organization, UserOrganization, Role
from sqlmodel import select

async def verify_permissions():
    async for session in get_session():
        print("--- Verifying Permissions ---")
        
        # 1. Get/Create Test Users
        # We need a fresh Member to be sure
        email = "verify_perm_member@test.com"
        stmt = select(User).where(User.email == email)
        member = (await session.exec(stmt)).first()
        
        if not member:
            member = User(email=email, full_name="Perm Member", hashed_password="pw")
            session.add(member)
            await session.commit()
            await session.refresh(member)
            
        # 2. Assign "Organization Member" Role
        # Ensure role exists
        role_stmt = select(Role).where(Role.name == "Organization Member")
        member_role = (await session.exec(role_stmt)).first()
        
        if not member_role:
            print("ERROR: 'Organization Member' role not found!")
            return
            
        # Create Org
        org = Organization(name="Perm Test Org")
        session.add(org)
        await session.commit()
        await session.refresh(org)
        
        # Link Member
        uo = UserOrganization(user_id=member.id, organization_id=org.id, role_id=member_role.id, role="member")
        session.add(uo)
        await session.commit()
        
        # 3. Check Permissions
        print(f"Checking permissions for {email} in Org {org.id}...")
        
        # Should NOT have 'org:manage_users'
        can_manage = await rbac_service.has_permission(session, member.id, "org:manage_users", org_id=org.id)
        print(f"Has 'org:manage_users': {can_manage}")
        
        if can_manage:
            print("❌ FAILURE: Member HAS admin permission 'org:manage_users'!")
        else:
            print("✅ SUCCESS: Member correctly denied 'org:manage_users'.")

        # 4. Check 'project:create_suite' (Editor/Admin only)
        # Member should NOT have this
        can_create_suite = await rbac_service.has_permission(session, member.id, "project:create_suite", org_id=org.id) # Scope usually project, but let's check generic?
        # RBAC service checks context.
        # Check Project level?
        # A member shouldn't even create projects.
        can_create_project = await rbac_service.has_permission(session, member.id, "org:create_project", org_id=org.id)
        print(f"Has 'org:create_project': {can_create_project}")
        
        if can_create_project:
             print("❌ FAILURE: Member HAS admin permission 'org:create_project'!")
        else:
             print("✅ SUCCESS: Member correctly denied 'org:create_project'.")

if __name__ == "__main__":
    asyncio.run(verify_permissions())
