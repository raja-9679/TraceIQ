from typing import List, Optional
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from app.models import (
    UserSystemRole, UserWorkspace, UserProjectAccess, 
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
        
        # 2. Workspace Roles
        ws_roles = await session.exec(select(UserWorkspace.role_id).where(UserWorkspace.user_id == user_id))
        role_ids.update(ws_roles.all())
        
        # 3. Project Roles (Direct)
        proj_roles = await session.exec(select(UserProjectAccess.role_id).where(UserProjectAccess.user_id == user_id))
        role_ids.update(proj_roles.all())
        
        # 4. Project Roles (via Team)
        user_teams_stmt = select(UserTeam.team_id).where(UserTeam.user_id == user_id)
        user_team_ids = (await session.exec(user_teams_stmt)).all()
        
        if user_team_ids:
            tpa_stmt = select(TeamProjectAccess.role_id).where(
                TeamProjectAccess.team_id.in_(user_team_ids),
                TeamProjectAccess.role_id != None
            )
            team_role_ids = (await session.exec(tpa_stmt)).all()
            role_ids.update(team_role_ids)

        if not role_ids:
            return []
            
        # Fetch permissions
        stmt = select(Permission.scope, Permission.action).join(RolePermission).where(RolePermission.role_id.in_(role_ids))
        results = await session.exec(stmt)
        return [f"{r[0]}:{r[1]}" for r in results.all()]

    async def has_permission(self, session: AsyncSession, user_id: int, permission: str, workspace_id: Optional[int] = None, project_id: Optional[int] = None) -> bool:
        """
        Check if user has a specific permission.
        Format: "scope:action" (e.g. "workspace:create_project")
        """
        req_scope, req_action = permission.split(":")
        
        # 1. Tenant Admin Check (Global Override for most things, or specific tenant scope)
        # Fix: Resolve Tenant ID from Workspace or Project
        tenant_id = None
        if workspace_id:
             # We need to fetch the Workspace to know which Tenant it belongs to
             from app.models import Workspace
             # Ideally cached or passed in, but for safety we fetch
             ws = await session.get(Workspace, workspace_id)
             if ws:
                 tenant_id = ws.tenant_id
        elif project_id:
             # Resolve via Project -> Workspace -> Tenant
             from app.models import Project, Workspace
             proj = await session.get(Project, project_id)
             if proj:
                 ws = await session.get(Workspace, proj.workspace_id)
                 if ws:
                     tenant_id = ws.tenant_id
        
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

        # 2. Workspace Scope Check
        # Ensure we have workspace_id if available via project
        if not workspace_id and project_id:
             from app.models import Project
             proj = await session.get(Project, project_id)
             if proj:
                 workspace_id = proj.workspace_id

        if workspace_id:
            ws_stmt = (
                select(Permission)
                .join(RolePermission)
                .join(Role)
                .join(UserWorkspace)
                .where(
                    UserWorkspace.user_id == user_id,
                    UserWorkspace.workspace_id == workspace_id,
                    Permission.action == req_action,
                    Permission.scope == req_scope
                )
            )
            if (await session.exec(ws_stmt)).first():
                return True

        # 3. Project Scope Check
        if project_id:
            project_role_ids = set()

            # A. Direct Project Access
            upa_stmt = select(UserProjectAccess).where(
                UserProjectAccess.user_id == user_id, 
                UserProjectAccess.project_id == project_id
            )
            upa = (await session.exec(upa_stmt)).first()
            if upa and upa.role_id:
                project_role_ids.add(upa.role_id)

            # B. Team Project Access
            user_teams_stmt = select(UserTeam.team_id).where(UserTeam.user_id == user_id)
            user_team_ids = (await session.exec(user_teams_stmt)).all()
            
            if user_team_ids:
                tpa_stmt = select(TeamProjectAccess).where(
                    TeamProjectAccess.project_id == project_id,
                    TeamProjectAccess.team_id.in_(user_team_ids)
                )
                tpas = (await session.exec(tpa_stmt)).all()
                for tpa in tpas:
                    if tpa.role_id:
                        project_role_ids.add(tpa.role_id)
            
            # Check permissions for these roles
            if project_role_ids:
                perm_stmt = (
                    select(Permission)
                    .join(RolePermission)
                    .where(
                        RolePermission.role_id.in_(project_role_ids),
                        Permission.action == req_action,
                        Permission.scope == req_scope
                     )
                )
                if (await session.exec(perm_stmt)).first():
                    return True

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
            "workspace": {},
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
        
        # 2. Workspace Permissions
        # We need to group by workspace_id
        ws_stmt = (
            select(UserWorkspace.workspace_id, Permission.scope, Permission.action)
            .join(Role, UserWorkspace.role_id == Role.id)
            .join(RolePermission, Role.id == RolePermission.role_id)
            .join(Permission, RolePermission.permission_id == Permission.id)
            .where(UserWorkspace.user_id == user_id)
        )
        ws_res = await session.exec(ws_stmt)
        for row in ws_res.all():
            ws_id, scope, action = row
            if ws_id not in permissions["workspace"]:
                permissions["workspace"][ws_id] = []
            permissions["workspace"][ws_id].append(f"{scope}:{action}")
            
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
            
        # Team Project Access
        # Fetch user teams
        ut_ids = (await session.exec(select(UserTeam.team_id).where(UserTeam.user_id == user_id))).all()
        if ut_ids:
                team_proj_stmt = (
                    select(TeamProjectAccess.project_id, Permission.scope, Permission.action)
                    .join(Role, TeamProjectAccess.role_id == Role.id)
                    .join(RolePermission, Role.id == RolePermission.role_id)
                    .join(Permission, RolePermission.permission_id == Permission.id)
                    .where(TeamProjectAccess.team_id.in_(ut_ids))
                )
                team_res = await session.exec(team_proj_stmt)
                for row in team_res.all():
                    pid, scope, action = row
                    if pid not in permissions["project"]:
                        permissions["project"][pid] = []
                    permissions["project"][pid].append(f"{scope}:{action}")
            
        return permissions

rbac_service = RBACService()
