"""What an API key may reach, enforced where access is decided.

An API key acts as the user who minted it, but it is minted FOR one workspace,
optionally narrowed to one project (`ApiKey.project_id`) and optionally capped
at a role (`ApiKey.role_id`). Until this module those three limits were only
honoured by the endpoints that remembered to call
`app.api.jobs.api_key_allows_project`; everywhere else (suites, cases, and the
many endpoints that check access with nothing but a user id) a key scoped to
project A read and wrote project B — and a key minted for workspace A reached
every other workspace its owner belonged to. The role was stored and never read.

So the limits ride on the request's session (`session.info`, like the RLS
scope — FastAPI hands the endpoint the same session the auth dependency saw)
and `access_service` / `rbac_service` consult them for every check made about
the key's owner. Checks about other users in the same request are untouched.
Without a scope on the session (JWT callers, Celery, scripts) nothing changes.

The per-endpoint `api_key_allows_project` checks stay: they also serve tests
and code paths that call endpoint functions directly with a principal.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Optional

_INFO_KEY = "traceiq_api_key_scope"

# Ceiling for the level-based checks in access_service, by role name. A role
# that is not listed (a custom role) caps at viewer: fail closed.
_LEVEL_BY_ROLE = {
    "Tenant Admin": 3,
    "Workspace Admin": 3,
    "Project Admin": 3,
    "Project Editor": 2,
    "Project Viewer": 1,
    "Workspace Member": 1,
}
LEVEL_NAMES = {3: "admin", 2: "editor", 1: "viewer"}


@dataclass(frozen=True)
class KeyScope:
    key_id: int
    user_id: int
    workspace_id: Optional[int]
    project_id: Optional[int]
    # None = no role on the key: it carries its owner's access unchanged.
    role_name: Optional[str] = None
    permissions: Optional[FrozenSet[str]] = None

    @property
    def level(self) -> int:
        if self.role_name is None:
            return 3
        return _LEVEL_BY_ROLE.get(self.role_name, 1)

    def allows_project(self, project_id: Optional[int],
                       project_workspace_id: Optional[int]) -> bool:
        if project_id is None:
            return False
        if self.workspace_id is not None and project_workspace_id != self.workspace_id:
            return False
        return self.project_id is None or self.project_id == project_id

    def allows_permission(self, permission: str) -> bool:
        return self.permissions is None or permission in self.permissions


async def bind(session, principal) -> Optional[KeyScope]:
    """Record the caller's key limits on the request session (or clear them)."""
    key = getattr(principal, "api_key", None)
    if key is None:
        session.info.pop(_INFO_KEY, None)
        return None
    role_name = None
    permissions = None
    if key.role_id is not None:
        from sqlmodel import select
        from app.models import Permission, Role, RolePermission
        role = await session.get(Role, key.role_id)
        # A role id that resolves to nothing grants nothing.
        role_name = role.name if role else ""
        rows = (await session.exec(
            select(Permission.scope, Permission.action)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == key.role_id))).all()
        permissions = frozenset(f"{s}:{a}" for s, a in rows)
    scope = KeyScope(key_id=key.id, user_id=principal.user.id,
                     workspace_id=key.workspace_id, project_id=key.project_id,
                     role_name=role_name, permissions=permissions)
    session.info[_INFO_KEY] = scope
    return scope


def for_user(session, user_id: int) -> Optional[KeyScope]:
    """The key limits that apply to an access check about `user_id`, if any."""
    info = getattr(session, "info", None)
    if not isinstance(info, dict):
        return None
    scope = info.get(_INFO_KEY)
    if scope is None or scope.user_id != user_id:
        return None
    return scope


async def permits(session, scope: KeyScope, permission: str,
                  workspace_id: Optional[int], project_id: Optional[int]) -> bool:
    """rbac_service.has_permission's key gate. A project-scoped key never
    passes a workspace- or tenant-level check; a check with no resource at all
    (tenant-wide actions) is outside every key."""
    if not scope.allows_permission(permission):
        return False
    if project_id is not None:
        from app.models import Project
        project = await session.get(Project, project_id)
        if project is None:
            return False
        if workspace_id is not None and workspace_id != project.workspace_id:
            return False
        return scope.allows_project(project.id, project.workspace_id)
    if workspace_id is not None:
        return scope.project_id is None and workspace_id == scope.workspace_id
    return False


def restrict(query, project_col, session, user_id: int):
    """Narrow a listing built from the owner's memberships to the key's own
    workspace/project. Listings never go through the per-id checks above."""
    ks = for_user(session, user_id)
    if ks is None:
        return query
    from sqlmodel import select
    from app.models import Project
    query = query.where(project_col.in_(
        select(Project.id).where(Project.workspace_id == ks.workspace_id)))
    if ks.project_id is not None:
        query = query.where(project_col == ks.project_id)
    return query
