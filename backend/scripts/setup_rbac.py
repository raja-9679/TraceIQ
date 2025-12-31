import asyncio
import os
import sys

# Ensure we can import from app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import select
from app.core.database import get_session_context
from app.models import (
    Permission, Role, RolePermission, 
    UserOrganization, User, Tenant, UserSystemRole,
    UserProjectAccess
)

# --- Definitions ---
PERMISSIONS = [
    # Tenant Level
    {"scope": "tenant", "action": "manage_settings", "resource": "tenant", "description": "Manage tenant settings"},
    {"scope": "tenant", "action": "create_org", "resource": "organization", "description": "Create new organizations"},
    
    # Org Level
    {"scope": "org", "action": "manage_users", "resource": "organization", "description": "Invite/Remove users in Org"},
    {"scope": "org", "action": "create_team", "resource": "team", "description": "Create teams"},
    {"scope": "org", "action": "create_project", "resource": "project", "description": "Create projects"},
    {"scope": "org", "action": "delete_org", "resource": "organization", "description": "Delete organization"},
    
    # Project Level
    {"scope": "project", "action": "manage_access", "resource": "project", "description": "Manage project access"},
    {"scope": "project", "action": "create_suite", "resource": "test_suite", "description": "Create test suites"},
    {"scope": "project", "action": "execute_test", "resource": "test_run", "description": "Execute tests"},
    {"scope": "project", "action": "view_report", "resource": "test_run", "description": "View test reports"},
]

ROLES = {
    "Tenant Admin": [
        "tenant:manage_settings", "tenant:create_org", 
        "org:manage_users", "org:create_team", "org:create_project", "org:delete_org",
        "project:manage_access", "project:create_suite", "project:execute_test", "project:view_report"
    ],
    "Organization Admin": [
        "org:manage_users", "org:create_team", "org:create_project",
        "project:manage_access", "project:create_suite", "project:execute_test", "project:view_report"
    ],
    "Organization Member": [
        "project:view_report" # Base access
    ],
    "Project Admin": [
        "project:manage_access", "project:create_suite", "project:execute_test", "project:view_report"
    ],
    "Project Editor": [
        "project:create_suite", "project:execute_test", "project:view_report"
    ],
    "Project Viewer": [
        "project:view_report"
    ]
}

async def setup_rbac():
    print("Starting RBAC Setup & Migration...")
    
    async with get_session_context() as session:
        # 1. Seed Permissions
        print("\n[Step 1] Seeding Permissions...")
        perm_map = {} # action -> Permission Object
        
        for p_def in PERMISSIONS:
            key = f"{p_def['scope']}:{p_def['action']}"
            stmt = select(Permission).where(
                Permission.scope == p_def['scope'], 
                Permission.action == p_def['action']
            )
            existing = (await session.exec(stmt)).first()
            
            if not existing:
                perm = Permission(**p_def)
                session.add(perm)
                await session.flush() # get ID
                await session.refresh(perm)
                print(f"  + Created Permission: {key}")
                perm_map[key] = perm
            else:
                perm_map[key] = existing

        # 2. Seed Roles
        print("\n[Step 2] Seeding Roles...")
        role_map = {} # name -> Role Object
        
        for role_name, perm_keys in ROLES.items():
            stmt = select(Role).where(Role.name == role_name, Role.tenant_id == None) # System Roles
            existing = (await session.exec(stmt)).first()
            
            if not existing:
                role = Role(name=role_name, description="System Role")
                session.add(role)
                await session.flush()
                await session.refresh(role)
                print(f"  + Created Role: {role_name}")
                role_map[role_name] = role
                
                # Assign Permissions
                for pk in perm_keys:
                    p_scope, p_action = pk.split(":")
                    perm = perm_map.get(pk)
                    if perm:
                        rp = RolePermission(role_id=role.id, permission_id=perm.id)
                        session.add(rp)
                print(f"  + Created Role: {role_name}")
                role_map[role_name] = role
            else:
                role_map[role_name] = existing
                role = existing

            # Assign Permissions (Idempotent)
            for pk in perm_keys:
                p_scope, p_action = pk.split(":")
                perm = perm_map.get(pk)
                if perm:
                    # Check if exists
                    rp_stmt = select(RolePermission).where(
                        RolePermission.role_id == role.id, 
                        RolePermission.permission_id == perm.id
                    )
                    existing_rp = (await session.exec(rp_stmt)).first()
                    if not existing_rp:
                        rp = RolePermission(role_id=role.id, permission_id=perm.id)
                        session.add(rp)
                        print(f"    + Added {pk} to {role_name}")

        await session.commit()
        
        # 3. Migrate Users (Organization Links)
        print("\n[Step 3] Migrating User Organization Links...")
        user_orgs = await session.exec(select(UserOrganization).where(UserOrganization.role_id == None))
        uos = user_orgs.all()
        
        count = 0
        for uo in uos:
            if uo.role == "admin":
                uo.role_id = role_map["Organization Admin"].id
                session.add(uo)
                count += 1
            elif uo.role == "member": # or anything else default
                uo.role_id = role_map["Organization Member"].id
                session.add(uo)
                count += 1
        
        print(f"  - Migrated {count} UserOrganization records.")
        
        # 4. Migrate Project Access
        print("\n[Step 4] Migrating User Project Access...")
        upas = (await session.exec(select(UserProjectAccess).where(UserProjectAccess.role_id == None))).all()
        
        count = 0
        for upa in upas:
            if upa.access_level == "admin":
                upa.role_id = role_map["Project Admin"].id
            elif upa.access_level == "editor":
                upa.role_id = role_map["Project Editor"].id
            else:
                upa.role_id = role_map["Project Viewer"].id
            session.add(upa)
            count += 1
            
        print(f"  - Migrated {count} UserProjectAccess records.")

        # 5. Migrate Tenant Owners
        print("\n[Step 5] Assigning Tenant Admin Roles...")
        tenants = (await session.exec(select(Tenant))).all()
        
        for t in tenants:
            # Check if owner has system role
            usr = await session.exec(select(UserSystemRole).where(
                UserSystemRole.user_id == t.owner_id, 
                UserSystemRole.role_id == role_map["Tenant Admin"].id
            ))
            if not usr.first():
                new_usr = UserSystemRole(
                    user_id=t.owner_id,
                    role_id=role_map["Tenant Admin"].id,
                    tenant_id=t.id
                )
                session.add(new_usr)
                print(f"  + Assigned Tenant Admin to User {t.owner_id} for Tenant {t.name}")

        await session.commit()
        print("\n✅ RBAC Setup Complete!")

if __name__ == "__main__":
    asyncio.run(setup_rbac())
