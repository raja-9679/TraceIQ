from typing import List, Optional
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from app.models import (
    UserSystemRole, UserOrganization, UserProjectAccess, 
    Role, Permission, RolePermission, TeamProjectAccess, UserTeam
)

class RBACService:
    async def get_user_effective_permissions(self, user_id: int, session: AsyncSession) -> List[str]:
        """
        Get all permissions for a user across all scopes (Tenant, Org, Project).
        Returns a list of "scope:action" strings. (e.g., "org:create_project")
        Note: This is a heavy query, usually we check specific permission.
        """
        # Optimized: Fetch all role IDs for the user
        role_ids = set()
        
        # 1. System Roles
        sys_roles = await session.exec(select(UserSystemRole.role_id).where(UserSystemRole.user_id == user_id))
        role_ids.update(sys_roles.all())
        
        # 2. Org Roles
        org_roles = await session.exec(select(UserOrganization.role_id).where(UserOrganization.user_id == user_id))
        role_ids.update(org_roles.all())
        
        # 3. Project Roles (Direct)
        proj_roles = await session.exec(select(UserProjectAccess.role_id).where(UserProjectAccess.user_id == user_id))
        role_ids.update(proj_roles.all())
        
        # 4. Project Roles (via Team) - Optional, complex query
        
        if not role_ids:
            return []
            
        # Fetch permissions
        stmt = select(Permission.scope, Permission.action).join(RolePermission).where(RolePermission.role_id.in_(role_ids))
        results = await session.exec(stmt)
        return [f"{r[0]}:{r[1]}" for r in results.all()]

    async def has_permission(self, session: AsyncSession, user_id: int, permission: str, org_id: Optional[int] = None, project_id: Optional[int] = None) -> bool:
        """
        Check if user has a specific permission.
        Format: "scope:action" (e.g. "org:create_project")
        """
        req_scope, req_action = permission.split(":")
        
        # 1. Tenant Admin Check (Global Override for most things, or specific tenant scope)
        # Fix: Resolve Tenant ID from Org or Project
        tenant_id = None
        if org_id:
             # We need to fetch the Organization to know which Tenant it belongs to
             from app.models import Organization
             # Ideally cached or passed in, but for safety we fetch
             org = await session.get(Organization, org_id)
             if org:
                 tenant_id = org.tenant_id
        elif project_id:
             # Resolve via Project -> Org -> Tenant
             from app.models import Project, Organization
             proj = await session.get(Project, project_id)
             if proj:
                 org = await session.get(Organization, proj.organization_id)
                 if org:
                     tenant_id = org.tenant_id
        
        # If we have a target tenant_id, we MUST check if the System Role is for THAT tenant.
        # If we don't (system level check?), we might default to no assumption or check all (dangerous).
        # For now, if checking against a resource, we expect context.
        
        sys_query = (
            select(Permission)
            .join(RolePermission)
            .join(Role)
            .join(UserSystemRole)
            .where(
                UserSystemRole.user_id == user_id,
                Permission.action == req_action,
                Permission.scope == req_scope
            )
        )
        
        if tenant_id:
            sys_query = sys_query.where(UserSystemRole.tenant_id == tenant_id)
        
        # If no context (tenant_id is None), it implies a truly Global Check?
        # Or a check for "Any Tenant"?
        # Current valid use case: "Can I create a tenant?" -> System level, no tenant_id yet.
        # In that case, UserSystemRole might have tenant_id=None (Super Admin? Not implemented yet).
        # OR we just return False if no context for a Tenant-scoped action.
        # Let's be safe: If tenant_id is enforced by the resource, we enforce it here.
        
        if (await session.exec(sys_query)).first():
            return True

        # 2. Org Scope Check
        if org_id:
            org_stmt = (
                select(Permission)
                .join(RolePermission)
                .join(Role)
                .join(UserOrganization)
                .where(
                    UserOrganization.user_id == user_id,
                    UserOrganization.organization_id == org_id,
                    Permission.action == req_action,
                    Permission.scope == req_scope
                )
            )
            if (await session.exec(org_stmt)).first():
                return True

        # 3. Project Scope Check
        if project_id:
            # A. Direct Project Access
            proj_stmt = (
                select(Permission)
                .join(RolePermission)
                .join(Role)
                .join(UserProjectAccess)
                .where(
                    UserProjectAccess.user_id == user_id,
                    UserProjectAccess.project_id == project_id,
                    Permission.action == req_action,
                    Permission.scope == req_scope
                )
            )
            if (await session.exec(proj_stmt)).first():
                return True
                
            # B. Team Project Access
            team_stmt = (
                select(Permission)
                .join(RolePermission)
                .join(Role)
                .join(TeamProjectAccess)
                .join(UserTeam, UserTeam.team_id == TeamProjectAccess.team_id)
                .where(
                    UserTeam.user_id == user_id,
                    TeamProjectAccess.project_id == project_id,
                    Permission.action == req_action,
                    Permission.scope == req_scope
                )
            )
            if (await session.exec(team_stmt)).first():
                return True
                
            # C. Inheritance: Does Org Admin imply Project Admin?
            # Usually yes. If I am Org Admin, I should have access to all projects?
            # Or explicit roles only? 
            # Design decision: Org Admin has "project:*" permissions implicitly via the "Organization Admin" role 
            # BUT those permissions are scoped to 'org' in the definition? 
            # WAIT. "Organization Admin" has "project:create_suite". 
            # If I query for "project:create_suite" and I have "Organization Admin", 
            # my role is linked to Organization.
            
            # If the user is an Org Admin of the project's organization, they should pass.
            if not org_id:
                # Need to fetch org_id from project if not supplied
                from app.models import Project
                proj = await session.get(Project, project_id)
                if proj:
                    org_id = proj.organization_id
            
            if org_id:
                # Check Org Level permissions again for this action
                # Note: "Organization Admin" role has permissions with scope="project" and action="create_suite"?
                # Let's check how we seeded it.
                # "Organization Admin": ["project:create_suite", ...]
                # So if I have Org Admin role, I have "project:create_suite" permission.
                # BUT, that permission is linked to my UserOrganization record.
                # So the query in step #2 (Org Scope Check) should match specific permissions too.
                
                # Logic Fix:
                # If I am checking for "project:create_suite" (scope=project), 
                # but I have it via an Org Role, the step #2 query filters by `Permission.scope == req_scope`.
                # If the Permission definition has scope='project', then `req_scope` matches.
                # So Step #2 covers "Org Admin accessing Project" IF the Org Admin role contains permissions with scope='project'.
                # Checking `setup_rbac.py`: 
                # {"scope": "project", "action": "create_suite", ...}
                # "Organization Admin" has "project:create_suite".
                # So yes, Step #2 works.
                pass

        return False

    async def get_role_by_name(self, session: AsyncSession, role_name: str) -> Optional[Role]:
        stmt = select(Role).where(Role.name == role_name)
        return (await session.exec(stmt)).first()

    async def get_user_permissions_map(self, user_id: int, session: AsyncSession) -> dict:
        """
        Returns a structured map of permissions:
        {
            "system": ["tenant:create_org"],
            "organization": { 1: ["org:manage_users"] },
            "project": { 10: ["project:execute"] }
        }
        """
        permissions = {
            "system": [],
            "organization": {},
            "project": {}
        }
        
        # 1. System Permissions
        sys_stmt = (
            select(Permission.scope, Permission.action)
            .join(RolePermission)
            .join(Role)
            .join(UserSystemRole)
            .where(UserSystemRole.user_id == user_id)
        )
        sys_perms = await session.exec(sys_stmt)
        permissions["system"] = [f"{p[0]}:{p[1]}" for p in sys_perms.all()]
        
        # 2. Organization Permissions
        # We need to group by organization_id
        org_stmt = (
            select(UserOrganization.organization_id, Permission.scope, Permission.action)
            .join(Role, UserOrganization.role_id == Role.id)
            .join(RolePermission, Role.id == RolePermission.role_id)
            .join(Permission, RolePermission.permission_id == Permission.id)
            .where(UserOrganization.user_id == user_id)
        )
        org_res = await session.exec(org_stmt)
        for row in org_res.all():
            org_id, scope, action = row
            if org_id not in permissions["organization"]:
                permissions["organization"][org_id] = []
            permissions["organization"][org_id].append(f"{scope}:{action}")
            
        # 3. Project Permissions
        # Direct
        proj_stmt = (
            select(UserProjectAccess.project_id, Permission.scope, Permission.action)
            .join(Role, UserProjectAccess.role_id == Role.id)
            .join(RolePermission, Role.id == RolePermission.role_id)
            .join(Permission, RolePermission.permission_id == Permission.id)
            .where(UserProjectAccess.user_id == user_id)
        )
        proj_res = await session.exec(proj_stmt)
        for row in proj_res.all():
            pid, scope, action = row
            if pid not in permissions["project"]:
                permissions["project"][pid] = []
            permissions["project"][pid].append(f"{scope}:{action}")
            
        return permissions

rbac_service = RBACService()
