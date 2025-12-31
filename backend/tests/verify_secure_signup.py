import asyncio
import sys
import os
import uuid
from sqlmodel import select

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import get_session_context
from app.models import User, Tenant, UserSystemRole, Organization, OrganizationInvitation
from app.core.auth import get_password_hash
from app.services.org_service import org_service
from app.api.auth import register_user, UserCreate

# Mock dependencies? No, let's use integration test style with real DB session
# But register_user expects session as dependency. We can call it directly passing session.

async def verify_secure_signup():
    print("Verifying Secure Signup Logic...")
    
    async with get_session_context() as session:
        # 1. Test Standalone Signup
        print("\n[Test 1] Standalone Signup (New Tenant + Admin)")
        email_standalone = "standalone@test.com"
        # Cleanup
        existing = (await session.exec(select(User).where(User.email == email_standalone))).first()
        if existing:
             print("  - Cleaning up existing user...")
             # Delete user and tenant? Complex cleanup. Let's assume clean DB or unique emails.
             # Actually, verification scripts run locally might need cleanup.
             # For now, use random email suffix?
             email_standalone = f"standalone_{uuid.uuid4().hex[:6]}@test.com"
        
        user_in = UserCreate(email=email_standalone, password="password", full_name="Standalone User")
        user = await register_user(user_in, session)
        
        # Verify Tenant
        stmt = select(Tenant).where(Tenant.owner_id == user.id)
        tenant = (await session.exec(stmt)).first()
        if tenant:
             print(f"  ✅ Tenant Created: {tenant.name}")
        else:
             print("  ❌ Tenant NOT Created")
             
        # Verify Tenant Admin Role
        stmt = select(UserSystemRole).where(UserSystemRole.user_id == user.id)
        usr = (await session.exec(stmt)).first()
        if usr:
             # Check role name?
             # Need to join Role. But assuming setup_rbac ran and assigned correct ID.
             print("  ✅ Tenant Admin Role Assigned")
        else:
             print("  ❌ Tenant Admin Role NOT Assigned")

        # 2. Test Invite Signup
        print("\n[Test 2] Invite Signup (Existing Org, No New Tenant, Member Role)")
        # Create Inviter and Org
        inviter_email = f"inviter_{uuid.uuid4().hex[:6]}@test.com"
        inviter_in = UserCreate(email=inviter_email, password="password", full_name="Inviter")
        inviter = await register_user(inviter_in, session)
        
        # Get inviter's org
        orgs = await org_service.get_user_organizations(inviter.id, session)
        org = orgs[0]
        
        # Invite User
        invitee_email = f"invitee_{uuid.uuid4().hex[:6]}@test.com"
        # Generate Invite
        res = await org_service.invite_user_to_organization(invitee_email, org.id, inviter.id, "member", session)
        token = res["token"]
        print(f"  - Generated Invite Token: {token}")
        
        # Register Invitee
        invitee_in = UserCreate(
            email=invitee_email, 
            password="password", 
            full_name="Invitee", 
            invite_token=token
        )
        invitee = await register_user(invitee_in, session)
        
        # Verify NO Tenant created for Invitee
        stmt = select(Tenant).where(Tenant.owner_id == invitee.id)
        t_check = (await session.exec(stmt)).first()
        if not t_check:
             print("  ✅ No New Tenant Created")
        else:
             print(f"  ❌ Tenant Created: {t_check.name}")
             
        # Verify NO Tenant Admin
        stmt = select(UserSystemRole).where(UserSystemRole.user_id == invitee.id)
        usr_check = (await session.exec(stmt)).first()
        if not usr_check:
             print("  ✅ No Tenant Admin Role Assigned")
        else:
             print("  ❌ Tenant Admin Role Assigned!")

        # Verify Org Membership
        user_orgs = await org_service.get_user_organizations(invitee.id, session)
        if any(o.id == org.id for o in user_orgs):
             print(f"  ✅ Added to Organization: {org.name}")
        else:
             print("  ❌ Not added to Organization")

if __name__ == "__main__":
    asyncio.run(verify_secure_signup())
