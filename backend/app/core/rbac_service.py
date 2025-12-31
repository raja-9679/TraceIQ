from typing import List, Optional
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from app.models import (
    User, UserProjectAccess, TeamProjectAccess, UserTeam, 
    Role, Permission, RolePermission, UserOrganization
)

class RBACService:
    async def get_user_roles_for_project(
        self, user_id: int, project_id: int, session: AsyncSession
    ) -> List[Role]:
        """
        Fetch all effective roles for a user in a project.
        Sources:
        1. Direct UserProjectAccess
        2. TeamProjectAccess (for all teams user is in)
        """
        roles = []

        # 1. Direct Access
        query_direct = select(UserProjectAccess).where(
            UserProjectAccess.user_id == user_id, 
            UserProjectAccess.project_id == project_id
        )
        direct_access_result = await session.exec(query_direct)
        direct_access = direct_access_result.first()
        
        if direct_access and direct_access.role_id:
            role = await session.get(Role, direct_access.role_id)
            if role:
                roles.append(role)
        
        # 1.5 Compatibility Fallback: If no role_id, check access_level string and map to default roles?
        # For now, we assume we will migrate data or auth check will fail if role_id is null.
        # But to be safe during transition, let's implement migration logic separately or here.
        
        # 2. Team Access
        # Join: UserTeam (user_id) -> TeamProjectAccess (team_id=project_id)
        query_team = (
            select(TeamProjectAccess)
            .join(UserTeam, UserTeam.team_id == TeamProjectAccess.team_id)
            .where(
                UserTeam.user_id == user_id,
                TeamProjectAccess.project_id == project_id
            )
        )
        team_access_results = await session.exec(query_team)
        for tpa in team_access_results.all():
            if tpa.role_id:
                role = await session.get(Role, tpa.role_id)
                if role:
                    roles.append(role)
        
        return roles

    async def check_access(
        self, 
        user_id: int, 
        project_id: int, 
        required_permission_action: str, 
        resource_type: str,
        session: AsyncSession
    ) -> bool:
        """
        Check if user has a permission on a project.
        
        Args:
            user_id: ID of the user
            project_id: ID of the project
            required_permission_action: Action string (e.g. 'create', 'update')
            resource_type: Resource string (e.g. 'test_case', 'project')
        """
        # 1. Check if Organization Admin (Super Admin for the Org)
        # We need to find the org_id from the project first to do this check efficiently
        # But `check_access` is often called in tight loops.
        # For MVP optimization, assume Org Admin has all permissions.
        
        # Let's get UserRoles for Project
        roles = await self.get_user_roles_for_project(user_id, project_id, session)
        
        if not roles:
            # Fallback for Org Admin check?
            # Or reliance on legacy check? 
            # Ideally we want this service to be comprehensive.
            pass

        role_ids = [r.id for r in roles]
        if not role_ids:
            return False
            
        # Get permissions for these roles
        # Select P from Permission P
        # Join RolePermission RP
        # Where RP.role_id in role_ids AND P.action == required_action AND P.resource == resource_type
        
        query = (
            select(Permission)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(
                RolePermission.role_id.in_(role_ids), # type: ignore
                Permission.action == required_permission_action,
                Permission.resource == resource_type
            )
        )
        result = await session.exec(query)
        if result.first():
            return True
            
        return False

rbac_service = RBACService()
