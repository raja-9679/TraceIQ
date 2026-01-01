from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from app.models import Role, Permission, RolePermission

INITIAL_RBAC = {
    "roles": {
        "Tenant Admin": {
            "description": "Administrator of the entire Tenant",
            "permissions": [
                "tenant:manage_settings",
                "tenant:manage_users",
                "tenant:create_workspace",
                "workspace:create",
                "workspace:manage_users",
                "workspace:manage_settings",
                "workspace:delete",
                "workspace:create_team",
                "project:create",
                "project:manage",
                "project:update",
                "project:manage_access",
                "project:view",
                "project:delete",
                "project:create_suite",
                "test:create",
                "test:run",
                "test:view"
            ]
        },
        "Workspace Admin": {
            "description": "Administrator of a Workspace",
            "permissions": [
                "workspace:manage_users",
                "workspace:manage_settings",
                "workspace:delete",
                "workspace:create_team",
                "project:create",
                "project:manage",
                "project:update",
                "project:manage_access",
                "project:view",
                "project:delete",
                "project:create_suite",
                "test:create",
                "test:run",
                "test:view"
            ]
        },
        "Workspace Member": {
            "description": "Member of a Workspace",
            "permissions": [
                "workspace:view",
                "project:view",
                "test:view"
            ]
        },
        "Project Admin": {
            "description": "Administrator of a Project",
            "permissions": [
                "project:manage",
                "project:update",
                "project:manage_access",
                "project:view",
                "project:delete",
                "project:create_suite",
                "test:create",
                "test:run",
                "test:view"
            ]
        },
        "Project Editor": {
            "description": "Editor of a Project",
            "permissions": [
                "project:view",
                "test:create",
                "test:run",
                "test:view"
            ]
        },
        "Project Viewer": {
            "description": "Viewer of a Project",
            "permissions": [
                "project:view",
                "test:view"
            ]
        }
    }
}

async def init_rbac(session: AsyncSession):
    print("Initializing RBAC...")
    
    # 1. Create Permissions
    all_permissions = set()
    for role_def in INITIAL_RBAC["roles"].values():
        all_permissions.update(role_def["permissions"])
    
    existing_perms = (await session.exec(select(Permission))).all()
    existing_perm_map = {f"{p.scope}:{p.action}": p for p in existing_perms}
    
    for perm_str in all_permissions:
        if perm_str not in existing_perm_map:
            scope, action = perm_str.split(":", 1)
            p = Permission(scope=scope, action=action, resource=scope, description=f"Permission to {action} {scope}")
            session.add(p)
            print(f"Created Permission: {perm_str}")
    
    await session.commit()
    
    # Refresh permissions map
    existing_perms = (await session.exec(select(Permission))).all()
    existing_perm_map = {f"{p.scope}:{p.action}": p for p in existing_perms}

    # 2. Create or Update Roles
    existing_roles = (await session.exec(select(Role))).all()
    existing_role_map = {r.name: r for r in existing_roles}
    
    for role_name, role_def in INITIAL_RBAC["roles"].items():
        if role_name not in existing_role_map:
            r = Role(name=role_name, description=role_def["description"])
            session.add(r)
            await session.commit()
            await session.refresh(r)
            existing_role_map[role_name] = r
            print(f"Created Role: {role_name}")
        
        # Always check/update permissions
        r = existing_role_map[role_name]
        
        # Get current permissions for this role
        current_role_perms = await session.exec(select(RolePermission).where(RolePermission.role_id == r.id))
        current_perm_ids = {rp.permission_id for rp in current_role_perms.all()}
        
        for perm_str in role_def["permissions"]:
            perm = existing_perm_map.get(perm_str)
            if perm and perm.id not in current_perm_ids:
                rp = RolePermission(role_id=r.id, permission_id=perm.id)
                session.add(rp)
                print(f"Added permission {perm_str} to Role {role_name}")
        
        await session.commit()

    print("RBAC Initialization Complete.")
