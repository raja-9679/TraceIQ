import asyncio
import sys
import os

# Add backend directory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlmodel import select
from app.core.database import get_session
from app.models import User, Organization, UserOrganization, UserProjectAccess, Project, Tenant, UserSystemRole
from app.services.org_service import org_service
from app.services.rbac_service import rbac_service

async def verify_visibility():
    async for session in get_session():
        print("--- Setting up Test Data ---")
        
        # 1. Create a Tenant Admin
        ta = User(email="tenant_admin_scope@test.com", full_name="TA Scope", hashed_password="pw")
        session.add(ta)
        
        # 2. Create an Org Admin
        oa = User(email="org_admin_scope@test.com", full_name="OA Scope", hashed_password="pw")
        session.add(oa)
        
        # 3. Create a Project Admin (User A)
        pa = User(email="proj_admin_scope@test.com", full_name="PA Scope", hashed_password="pw")
        session.add(pa)
        
        # 4. Create a Project Member (User B) - in same project
        pm = User(email="proj_member_same@test.com", full_name="PM Same", hashed_password="pw")
        session.add(pm)
        
        # 5. Create an Unrelated Member (User C) - in same org but different project
        um = User(email="unrelated_scope@test.com", full_name="Unrelated", hashed_password="pw")
        session.add(um)
        
        await session.commit()
        for u in [ta, oa, pa, pm, um]: await session.refresh(u)

        # Setup Tenant
        tenant = Tenant(name="Scope Tenant", owner_id=ta.id)
        session.add(tenant)
        await session.commit()
        await session.refresh(tenant)
        
        # Assign TA Role to Tenant
        ta_role = await rbac_service.get_role_by_name(session, "Tenant Admin")
        session.add(UserSystemRole(user_id=ta.id, role_id=ta_role.id, tenant_id=tenant.id))
        await session.commit()
        
        # Setup Org linked to Tenant
        org = await org_service.create_organization(name="Scope Org", owner_id=ta.id, session=session, tenant_id=tenant.id)
        
        # Setup Roles & Memberships
        # TA -> Tenant Admin (Done via UserSystemRole)
        # OA -> Org Admin (Invite as admin)
        # PA -> Project Admin (Invite as member, promote later)
        
        # Add everyone to Org
        await org_service.invite_user_to_organization(oa.email, org.id, ta.id, "admin", session)
        await org_service.invite_user_to_organization(pa.email, org.id, ta.id, "member", session) # Role "member" but will add proj admin later
        await org_service.invite_user_to_organization(pm.email, org.id, ta.id, "member", session)
        await org_service.invite_user_to_organization(um.email, org.id, ta.id, "member", session)
        
        # Setup Projects
        p1 = await org_service.create_project(name="Project 1", org_id=org.id, creator_id=ta.id, session=session)
        p2 = await org_service.create_project(name="Project 2", org_id=org.id, creator_id=ta.id, session=session)
        
        # Add PA and PM to P1
        # PA is Admin of P1
        # PM is Member of P1
        session.add(UserProjectAccess(user_id=pa.id, project_id=p1.id, access_level="admin")) 
        session.add(UserProjectAccess(user_id=pm.id, project_id=p1.id, access_level="editor"))
        
        # Default implicit RBAC: 
        # But `org_service` checks `has_permission`.
        # We need to ensure `has_permission` returns True for PA on "project:manage_access" (admin) 
        # AND False for "org:manage_users".
        
        await session.commit()
        
        print("\n--- Verifying Scoping ---")
        
        # 1. Tenant Admin View
        print("Checking Tenant Admin View...")
        ta_view = await org_service.get_tenant_users_detailed([tenant.id], session) 
        print(f"TA View Count: {len(ta_view)}")
        assert len(ta_view) >= 5, f"TA should see 5 users. Got {len(ta_view)}"
        
        # 2. Org Admin View
        # OA should see EVERYONE in Org.
        print("Checking Org Admin View...")
        # Mock permission check or rely on Role assignment?
        # `invite_user_to_organization` with role="admin" assigns "Organization Admin" role ID.
        # Verify OA sees 5.
        oa_view = await org_service.get_org_members_detailed(org.id, session, oa.id)
        print(f"OA View Count: {len(oa_view)}")
        assert len(oa_view) == 5, f"OA should see 5 users. Got {len(oa_view)}"
        
        # 3. Project Admin View
        # PA (Admin of P1) should see: PA (self), PM (in P1).
        # Unrelated member (UM) is NOT in P1.
        # OA and TA are in Org, but are they in P1? No.
        # So PA should see PA + PM. Maybe OA/TA if they are implicitly in projects?
        # Currently, they are not added to UserProjectAccess.
        # So PA should see 2 people (Self + PM).
        
        print("Checking Project Admin View...")
        # Need to ensure PA has "Project Admin" role for permission checks inside service.
        # Our service logic checks: 
        # is_tenant_admin?
        # is_org_admin? 
        # If neither -> filter by projects.
        
        # Ensure PA is NOT Org Admin. We invited as "member".
        pa_view = await org_service.get_org_members_detailed(org.id, session, pa.id)
        
        print(f"PA View Count: {len(pa_view)}")
        pa_ids = [u['id'] for u in pa_view]
        
        # Assertions
        assert pa.id in pa_ids, "PA should see self"
        assert pm.id in pa_ids, "PA should see PM (same project)"
        assert um.id not in pa_ids, "PA should NOT see Unrelated Member (diff project)"
        
        # What about OA/TA? They are not in the project. Should PA see them?
        # Logic says: "Select users ... join UserProjectAccess ... where project_id in viewer_projects".
        # Since OA/TA are NOT in UserProjectAccess for P1, they are NOT visible.
        # This matches STRICT scoping requested ("only the project users should be visible").
        assert oa.id not in pa_ids, "PA should NOT see Org Admin (not in project)"
        
        print("SUCCESS! All visibility rules verified.")

if __name__ == "__main__":
    asyncio.run(verify_visibility())
