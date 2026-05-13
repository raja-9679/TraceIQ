from typing import Optional
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, or_, and_
from app.models import (
    User, UserWorkspace, Workspace, Project, Team, Role,
    UserProjectAccess, TeamProjectAccess, TestCase, UserTestCaseAccess
)

# ROLE HIERARCHY used for access comparisons
_ROLE_MAP = {"admin": 3, "editor": 2, "viewer": 1}

# Maps Role.name → legacy access_level string (for rows that have role_id set)
_ROLE_NAME_TO_LEVEL = {
    "Workspace Admin": "admin",
    "Workspace Member": "member",
    "Project Admin": "admin",
    "Project Editor": "editor",
    "Project Viewer": "viewer",
}


async def _workspace_role_level(uw: UserWorkspace, session: AsyncSession) -> int:
    """Return numeric role level for a UserWorkspace row, using role_id when available."""
    if uw.role_id is not None:
        role = await session.get(Role, uw.role_id)
        legacy = _ROLE_NAME_TO_LEVEL.get(role.name, "member") if role else "member"
        return _ROLE_MAP.get(legacy, 0)
    return _ROLE_MAP.get(uw.role, 0)


async def _project_access_level(pa, session: AsyncSession) -> str:
    """Return access_level string for a UserProjectAccess or TeamProjectAccess row."""
    if pa.role_id is not None:
        role = await session.get(Role, pa.role_id)
        if role:
            return _ROLE_NAME_TO_LEVEL.get(role.name, pa.access_level)
    return pa.access_level


class AccessService:
    @staticmethod
    async def has_project_access(user_id: int, project_id: int, session: AsyncSession, min_role: str = "viewer") -> bool:
        min_val = _ROLE_MAP.get(min_role, 1)

        # 1. Check if user is Workspace Admin
        project = await session.get(Project, project_id)
        if not project:
            return False

        ws_rows = await session.exec(
            select(UserWorkspace)
            .where(
                UserWorkspace.user_id == user_id,
                UserWorkspace.workspace_id == project.workspace_id,
            )
        )
        for uw in ws_rows.all():
            if await _workspace_role_level(uw, session) >= _ROLE_MAP["admin"]:
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
            level = await _project_access_level(ua, session)
            if _ROLE_MAP.get(level, 1) >= min_val:
                return True

        # 3. Check Team access to Project — filter by allowed roles in SQL
        from app.models import UserTeam
        allowed_roles = [r for r, v in _ROLE_MAP.items() if v >= min_val]
        team_access = await session.exec(
            select(TeamProjectAccess)
            .join(UserTeam, UserTeam.team_id == TeamProjectAccess.team_id)
            .where(
                UserTeam.user_id == user_id,
                TeamProjectAccess.project_id == project_id,
                TeamProjectAccess.access_level.in_(allowed_roles)
            )
            .limit(1)
        )
        if team_access.first():
            return True

        return False

    @staticmethod
    async def get_project_role(user_id: int, project_id: int, session: AsyncSession) -> Optional[str]:
        from app.models import UserTeam
        project = await session.get(Project, project_id)
        if not project:
            return None

        # Check Workspace Admin first
        ws_rows = await session.exec(
            select(UserWorkspace)
            .where(
                UserWorkspace.user_id == user_id,
                UserWorkspace.workspace_id == project.workspace_id,
            )
        )
        for uw in ws_rows.all():
            if await _workspace_role_level(uw, session) >= _ROLE_MAP["admin"]:
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
        effective_role = (await _project_access_level(ua, session)) if ua else None

        # Check Team access (keep highest role)
        team_access = await session.exec(
            select(TeamProjectAccess)
            .join(UserTeam, UserTeam.team_id == TeamProjectAccess.team_id)
            .where(
                UserTeam.user_id == user_id,
                TeamProjectAccess.project_id == project_id
            )
        )
        for ta in team_access.all():
            ta_level = await _project_access_level(ta, session)
            if not effective_role or _ROLE_MAP.get(ta_level, 0) > _ROLE_MAP.get(effective_role, 0):
                effective_role = ta_level

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
