from typing import Optional
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, or_, and_
from app.services import key_scope
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

        # An API key never reaches past its workspace/project or above its role.
        ks = key_scope.for_user(session, user_id)
        if ks is not None and (not ks.allows_project(project.id, project.workspace_id)
                               or min_val > ks.level):
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
        role = await AccessService._owner_project_role(user_id, project_id, session)
        ks = key_scope.for_user(session, user_id)
        if role is None or ks is None:
            return role
        project = await session.get(Project, project_id)
        if not ks.allows_project(project_id, project.workspace_id if project else None):
            return None
        # Capped at the key's role: a viewer key of an admin is a viewer.
        if _ROLE_MAP.get(role, 0) > ks.level:
            return key_scope.LEVEL_NAMES[ks.level]
        return role

    @staticmethod
    async def _owner_project_role(user_id: int, project_id: int, session: AsyncSession) -> Optional[str]:
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
        # An API key is held to its project scope, so the per-case override
        # below must not let it through first: go straight to the project
        # check, which applies the key's limits.
        if key_scope.for_user(session, user_id) is not None:
            case = await session.get(TestCase, test_case_id)
            if not case:
                return False
            project_id = case.project_id
            if not project_id and case.test_suite_id:
                from app.models import TestSuite
                suite = await session.get(TestSuite, case.test_suite_id)
                project_id = suite.project_id if suite else None
            if not project_id:
                return False
            return await AccessService.has_project_access(user_id, project_id, session, min_role)

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

    # --- workspace-level checks -------------------------------------------
    # Endpoints that gate on "is the caller a member / admin of this
    # workspace" use these instead of querying UserWorkspace themselves, so an
    # API key's workspace / project / role limits (app/services/key_scope.py)
    # apply to them as they do to project checks.

    @staticmethod
    async def workspace_membership(user_id: int, workspace_id: Optional[int],
                                   session: AsyncSession, *, project_id: Optional[int] = None,
                                   per_project: bool = False,
                                   write: bool = False) -> Optional[UserWorkspace]:
        """The caller's UserWorkspace row, or None when they are not a member
        or their API key does not reach this workspace (see
        key_scope.allows_workspace for `project_id` / `per_project`). `write`
        applies the key's role ceiling for a write (editor or above)."""
        if workspace_id is None:
            return None
        if not key_scope.allows_workspace(session, user_id, workspace_id,
                                          min_level=2 if write else 1,
                                          project_id=project_id, per_project=per_project):
            return None
        return (await session.exec(select(UserWorkspace).where(
            UserWorkspace.user_id == user_id,
            UserWorkspace.workspace_id == workspace_id))).first()

    @staticmethod
    async def is_workspace_admin(user_id: int, workspace_id: Optional[int],
                                 session: AsyncSession) -> bool:
        """Workspace admin by membership role (role_id first, then the legacy
        role string). An API key passes only when it is workspace-wide, for
        this workspace, and its role (if any) is an admin role."""
        if workspace_id is None:
            return False
        if not key_scope.allows_workspace(session, user_id, workspace_id, min_level=3):
            return False
        for uw in (await session.exec(select(UserWorkspace).where(
                UserWorkspace.user_id == user_id,
                UserWorkspace.workspace_id == workspace_id))).all():
            if await _workspace_role_level(uw, session) >= _ROLE_MAP["admin"]:
                return True
        return False

access_service = AccessService()
