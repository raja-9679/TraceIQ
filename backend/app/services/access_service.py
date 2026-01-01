from typing import Optional
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, or_, and_
from app.models import (
    User, UserWorkspace, Workspace, Project, Team, 
    UserProjectAccess, TeamProjectAccess, TestCase, UserTestCaseAccess
)

class AccessService:
    @staticmethod
    async def has_project_access(user_id: int, project_id: int, session: AsyncSession, min_role: str = "viewer") -> bool:
        # ROLE HIERARCHY: admin (3) > editor (2) > viewer (1)
        role_map = {"admin": 3, "editor": 2, "viewer": 1}
        min_val = role_map.get(min_role, 1)

        # 1. Check if user is Workspace Admin
        project = await session.get(Project, project_id)
        if not project:
            return False
            
        ws_access = await session.exec(
            select(UserWorkspace)
            .where(
                UserWorkspace.user_id == user_id,
                UserWorkspace.workspace_id == project.workspace_id,
                UserWorkspace.role == "admin"
            )
        )
        if ws_access.first():
            return True
            
        # 2. Check direct User access to Project
        user_access = await session.exec(
            select(UserProjectAccess)
            .where(
                UserProjectAccess.user_id == user_id,
                UserProjectAccess.project_id == project_id
            )
        )
        ua = user_access.first()
        if ua:
            if role_map.get(ua.access_level, 1) >= min_val:
                return True
                
        # 3. Check Team access to Project
        from app.models import UserTeam
        team_access = await session.exec(
            select(TeamProjectAccess)
            .join(UserTeam, UserTeam.team_id == TeamProjectAccess.team_id)
            .where(
                UserTeam.user_id == user_id,
                TeamProjectAccess.project_id == project_id
            )
        )
        for ta in team_access.all():
            if role_map.get(ta.access_level, 1) >= min_val:
                return True
                
        return False

    @staticmethod
    async def get_project_role(user_id: int, project_id: int, session: AsyncSession) -> Optional[str]:
        # Check Workspace Admin first
        from app.models import Project, UserWorkspace, UserProjectAccess, TeamProjectAccess
        project = await session.get(Project, project_id)
        if not project:
            return None
            
        ws_access = await session.exec(
            select(UserWorkspace)
            .where(
                UserWorkspace.user_id == user_id,
                UserWorkspace.workspace_id == project.workspace_id,
                UserWorkspace.role == "admin"
            )
        )
        if ws_access.first():
            return "admin"
            
        # Check direct User access
        user_access = await session.exec(
            select(UserProjectAccess)
            .where(
                UserProjectAccess.user_id == user_id,
                UserProjectAccess.project_id == project_id
            )
        )
        ua = user_access.first()
        effective_role = ua.access_level if ua else None
        
        # Check Team access
        from app.models import UserTeam
        team_access = await session.exec(
            select(TeamProjectAccess)
            .join(UserTeam, UserTeam.team_id == TeamProjectAccess.team_id)
            .where(
                UserTeam.user_id == user_id,
                TeamProjectAccess.project_id == project_id
            )
        )
        
        role_map = {"admin": 3, "editor": 2, "viewer": 1}
        for ta in team_access.all():
            if not effective_role or role_map.get(ta.access_level, 0) > role_map.get(effective_role, 0):
                effective_role = ta.access_level
                
        return effective_role

    @staticmethod
    async def has_test_case_access(user_id: int, test_case_id: int, session: AsyncSession, min_role: str = "viewer") -> bool:
        # Overrides logic: Check TestCase level first
        tc_access = await session.exec(
            select(UserTestCaseAccess)
            .where(
                UserTestCaseAccess.user_id == user_id,
                UserTestCaseAccess.test_case_id == test_case_id
            )
        )
        tca = tc_access.first()
        if tca:
            if min_role == "viewer" or tca.access_level == "editor":
                return True
        
        # If no override, check Project level access
        case = await session.get(TestCase, test_case_id)
        if not case or not case.project_id:
            # Fallback to suite's project if available
            if case and case.test_suite:
                suite = case.test_suite
                if suite.project_id:
                    return await AccessService.has_project_access(user_id, suite.project_id, session, min_role)
            return False
            
        return await AccessService.has_project_access(user_id, case.project_id, session, min_role)

access_service = AccessService()
