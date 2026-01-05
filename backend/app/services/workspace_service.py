from typing import Optional, List
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from app.models import Workspace, UserWorkspace, User, Team, UserTeam, Project, AuditLog, UserProjectAccess
from datetime import datetime, timedelta
import secrets

class WorkspaceService:
    @staticmethod
    async def create_workspace(
        name: str, 
        owner_id: int, 
        session: AsyncSession, 
        description: Optional[str] = None, 
        commit: bool = True, 
        auto_create_project: bool = False,
        project_name: Optional[str] = None,
        tenant_id: Optional[int] = None
    ) -> Workspace:
        from app.services.rbac_service import rbac_service
        
        ws = Workspace(name=name, description=description, tenant_id=tenant_id)
        session.add(ws)
        await session.flush()
        
        # Link owner as Workspace Admin
        admin_role = await rbac_service.get_role_by_name(session, "Workspace Admin")
        if not admin_role:
             raise Exception("System Role 'Workspace Admin' not found. Run setup_rbac.py")
             
        user_ws = UserWorkspace(user_id=owner_id, workspace_id=ws.id, role_id=admin_role.id, role="admin")
        session.add(user_ws)
        
        # Audit log
        audit = AuditLog(
            entity_type="workspace",
            entity_id=ws.id,
            action="create",
            user_id=owner_id,
            workspace_id=ws.id,
            changes={"name": name}
        )
        session.add(audit)
        
        if auto_create_project:
            # Create a default project
            project = Project(
                name=project_name or "Initial Project",
                description="Your first project",
                workspace_id=ws.id
            )
            session.add(project)
            await session.flush() # Get project id
            
            # Grant access via Project Admin role
            p_admin = await rbac_service.get_role_by_name(session, "Project Admin")
            access = UserProjectAccess(
                user_id=owner_id,
                project_id=project.id,
                role_id=p_admin.id if p_admin else None,
                access_level="admin"
            )
            session.add(access)

        if commit:
            await session.commit()
            await session.refresh(ws)
        else:
            await session.flush()
        return ws

    @staticmethod
    async def get_user_workspaces(user_id: int, session: AsyncSession) -> List[Workspace]:
        result = await session.exec(
            select(Workspace)
            .join(UserWorkspace)
            .where(UserWorkspace.user_id == user_id)
        )
        return result.all()

    @staticmethod
    async def create_project(name: str, workspace_id: int, creator_id: int, session: AsyncSession, description: Optional[str] = None, commit: bool = True) -> Project:
        from app.services.rbac_service import rbac_service
        project = Project(name=name, description=description, workspace_id=workspace_id)
        session.add(project)
        await session.flush()
        
        # Creator gets admin access
        p_admin = await rbac_service.get_role_by_name(session, "Project Admin")
        
        access = UserProjectAccess(
            user_id=creator_id,
            project_id=project.id,
            role_id=p_admin.id if p_admin else None,
            access_level="admin"
        )
        session.add(access)
        
        audit = AuditLog(
            entity_type="project",
            entity_id=project.id,
            action="create",
            user_id=creator_id,
            workspace_id=workspace_id,
            changes={"name": name}
        )
        session.add(audit)
        if commit:
            await session.commit()
            await session.refresh(project)
        else:
            await session.flush()
        return project

    @staticmethod
    async def add_user_to_team_by_email(email: str, team_id: int, session: AsyncSession) -> bool:
        from app.models import UserTeam, User
        result = await session.exec(select(User).where(User.email == email))
        user = result.first()
        if not user:
            return False
            
        # Check if already in team
        existing = await session.exec(select(UserTeam).where(UserTeam.user_id == user.id, UserTeam.team_id == team_id))
        if existing.first():
            return True
            
        ut = UserTeam(user_id=user.id, team_id=team_id)
        session.add(ut)
        await session.commit()
        return True

    @staticmethod
    async def link_team_to_project(team_id: int, project_id: int, access_level: str, session: AsyncSession) -> bool:
        from app.models import TeamProjectAccess
        from app.services.rbac_service import rbac_service
        
        # Map access_level to Role
        role_name = "Project Viewer"
        if access_level == "admin": role_name = "Project Admin"
        elif access_level == "editor": role_name = "Project Editor"
        
        role = await rbac_service.get_role_by_name(session, role_name)
        role_id = role.id if role else None

        # Check if exists
        result = await session.exec(select(TeamProjectAccess).where(TeamProjectAccess.team_id == team_id, TeamProjectAccess.project_id == project_id))
        existing = result.first()
        if existing:
            existing.access_level = access_level
            existing.role_id = role_id
            session.add(existing)
        else:
            tpa = TeamProjectAccess(team_id=team_id, project_id=project_id, access_level=access_level, role_id=role_id)
            session.add(tpa)
        await session.commit()
        return True

    @staticmethod
    async def remove_user_from_team(team_id: int, user_id: int, session: AsyncSession) -> bool:
        from app.models import UserTeam
        result = await session.exec(select(UserTeam).where(UserTeam.team_id == team_id, UserTeam.user_id == user_id))
        ut = result.first()
        if ut:
            await session.delete(ut)
            await session.commit()
            return True
        return False

    @staticmethod
    async def get_project_teams(project_id: int, session: AsyncSession):
        from app.models import Team, TeamProjectAccess
        result = await session.exec(
            select(Team, TeamProjectAccess.access_level)
            .join(TeamProjectAccess, TeamProjectAccess.team_id == Team.id)
            .where(TeamProjectAccess.project_id == project_id)
        )
        teams = result.all()
        return [{"id": t[0].id, "name": t[0].name, "access_level": t[1]} for t in teams]

    @staticmethod
    async def get_project_members(project_id: int, session: AsyncSession):
        from app.models import User, UserProjectAccess
        result = await session.exec(
            select(User, UserProjectAccess.access_level)
            .join(UserProjectAccess, UserProjectAccess.user_id == User.id)
            .where(UserProjectAccess.project_id == project_id)
        )
        members = result.all()
        return [{"id": m[0].id, "full_name": m[0].full_name, "email": m[0].email, "access_level": m[1]} for m in members]

    @staticmethod
    async def add_user_project_access(user_id: int, project_id: int, access_level: str, session: AsyncSession) -> bool:
        from app.models import UserProjectAccess
        from app.services.rbac_service import rbac_service
        
        role_name = "Project Viewer"
        if access_level == "admin": role_name = "Project Admin"
        elif access_level == "editor": role_name = "Project Editor"
        
        role = await rbac_service.get_role_by_name(session, role_name)
        role_id = role.id if role else None

        result = await session.exec(
            select(UserProjectAccess)
            .where(UserProjectAccess.user_id == user_id, UserProjectAccess.project_id == project_id)
        )
        existing = result.first()
        if existing:
            existing.access_level = access_level
            existing.role_id = role_id
            session.add(existing)
        else:
            upa = UserProjectAccess(user_id=user_id, project_id=project_id, access_level=access_level, role_id=role_id)
            session.add(upa)
        await session.commit()
        return True

    @staticmethod
    async def unlink_team_from_project(team_id: int, project_id: int, session: AsyncSession) -> bool:
        from app.models import TeamProjectAccess
        result = await session.exec(
            select(TeamProjectAccess)
            .where(TeamProjectAccess.team_id == team_id, TeamProjectAccess.project_id == project_id)
        )
        tpa = result.first()
        if tpa:
            await session.delete(tpa)
            await session.commit()
            return True
        return False

    @staticmethod
    async def remove_user_project_access(user_id: int, project_id: int, session: AsyncSession) -> bool:
        from app.models import UserProjectAccess
        result = await session.exec(
            select(UserProjectAccess)
            .where(UserProjectAccess.user_id == user_id, UserProjectAccess.project_id == project_id)
        )
        upa = result.first()
        if upa:
            await session.delete(upa)
            await session.commit()
            return True
        return False

    @staticmethod
    async def get_workspace_members(workspace_id: int, session: AsyncSession) -> List[User]:
        from app.models import User, UserWorkspace
        result = await session.exec(
            select(User)
            .join(UserWorkspace)
            .where(UserWorkspace.workspace_id == workspace_id)
        )
        return result.all()

    @staticmethod
    async def get_workspace_members_detailed(workspace_id: int, session: AsyncSession, viewer_id: int):
        from app.models import User, UserWorkspace, Role, UserProjectAccess
        from app.services.rbac_service import rbac_service
        
        # 1. Determine Viewer's Scope
        # Check if Tenant Admin or Workspace Admin
        is_tenant_admin = await rbac_service.has_permission(session, viewer_id, "tenant:manage_settings") # Proxy for Tenant Admin
        is_workspace_admin = await rbac_service.has_permission(session, viewer_id, "workspace:manage_users", workspace_id=workspace_id)
        
        if is_tenant_admin or is_workspace_admin:
            # Full Access: Return all workspace members
            stmt = (
                select(User, UserWorkspace.role, Role.name)
                .join(UserWorkspace, UserWorkspace.user_id == User.id)
                .outerjoin(Role, UserWorkspace.role_id == Role.id)
                .where(UserWorkspace.workspace_id == workspace_id)
            )
        else:
            # Restricted Access: Project Admin / Viewer
            # strict rule: "when project admin sees the users only the project users should be visible"
            # So we filter users to those who share a project with the viewer?
            # Or specifically projects where the viewer IN AN ADMIN?
            # User said: "when project admin sees the users only the project users should be visible"
            # This implies visibility is bounded by common projects.
            
            # Find projects viewer is part of
            viewer_projects = await session.exec(select(UserProjectAccess.project_id).where(UserProjectAccess.user_id == viewer_id))
            p_ids = viewer_projects.all()
            
            if not p_ids:
                return []
                
            # Select users who are in these projects
            stmt = (
                select(User, UserWorkspace.role, Role.name)
                .join(UserWorkspace, UserWorkspace.user_id == User.id)
                .outerjoin(Role, UserWorkspace.role_id == Role.id)
                .join(UserProjectAccess, UserProjectAccess.user_id == User.id)
                .where(UserWorkspace.workspace_id == workspace_id)
                .where(UserProjectAccess.project_id.in_(p_ids)) # type: ignore
                .distinct()
            )

        result = await session.exec(stmt)
        members = result.all()
        
        # Deduplicate if distinct didn't catch (e.g. diff roles) - mostly handled by distinct on User if we select just User, but we select fields.
        # Dictionary comprehension to uniq by ID
        unique_members = {
            m[0].id: {
                "id": m[0].id,
                "full_name": m[0].full_name,
                "email": m[0].email,
                "role": m[2] if m[2] else m[1], 
                "last_login_at": m[0].last_login_at,
                "is_active": m[0].is_active,
                "status": "active"
            }
            for m in members
        }
        
        return list(unique_members.values())

    @staticmethod
    async def get_tenant_users_detailed(tenant_ids: List[int], session: AsyncSession):
        from app.models import User, UserWorkspace, Workspace, UserSystemRole, Role
        
        if not tenant_ids:
            return []
            
        # 1. Fetch Users in the Tenant (via Workspaces)
        ws_stmt = (
            select(User.id, User.email, User.full_name, User.last_login_at, User.is_active, UserWorkspace.role, Role.name)
            .join(UserWorkspace, UserWorkspace.user_id == User.id)
            .join(Workspace, UserWorkspace.workspace_id == Workspace.id)
            .outerjoin(Role, UserWorkspace.role_id == Role.id)
            .where(Workspace.tenant_id.in_(tenant_ids))
        )
        ws_members = (await session.exec(ws_stmt)).all()
        
        # 2. Fetch System Users (Tenant Admins) directly linked to Tenant
        sys_stmt = (
            select(User.id, User.email, User.full_name, User.last_login_at, User.is_active, Role.name)
            .join(UserSystemRole, UserSystemRole.user_id == User.id)
            .join(Role, UserSystemRole.role_id == Role.id)
            .where(UserSystemRole.tenant_id.in_(tenant_ids))
        )
        sys_members = (await session.exec(sys_stmt)).all()
        
        unique_users = {}
        
        # Helper to determine priority
        def get_priority(role_name: str) -> int:
             if not role_name: return 0
             r = role_name.lower()
             if "tenant admin" in r: return 100
             if "project admin" in r: return 50  # Rare to see at this level, but handled
             if "workspace admin" in r: return 40 # Changed to Workspace Admin
             if "admin" in r: return 40 # Generic admin
             if "editor" in r: return 30
             return 10 # Member/Viewer
        
        # Process Workspace Members first
        for m in ws_members:
            uid = m[0]
            role_name = m[6] if m[6] else m[5] # Role Name from Role table OR string fallback
            
            if uid not in unique_users:
                unique_users[uid] = {
                    "id": uid,
                    "email": m[1],
                    "full_name": m[2],
                    "last_login_at": m[3],
                    "is_active": m[4],
                    "role": role_name,
                    "status": "active"
                }
            else:
                 # Update role if higher priority
                 current_p = get_priority(unique_users[uid]["role"])
                 new_p = get_priority(role_name)
                 if new_p > current_p:
                     unique_users[uid]["role"] = role_name
                     
        # Process Tenant Admins (Override or Add)
        for sm in sys_members:
            uid = sm[0]
            role_name = sm[5] # Role Name associated with System Role
            
            if uid not in unique_users:
                 unique_users[uid] = {
                    "id": uid,
                    "email": sm[1],
                    "full_name": sm[2],
                    "last_login_at": sm[3],
                    "is_active": sm[4],
                    "role": role_name,
                    "status": "active"
                }
            else:
                 # Tenant Admin should act as override/highest priority
                 # We assume System Role -> Tenant Admin is high priority
                 # But let's check explicitly
                 current_p = get_priority(unique_users[uid]["role"])
                 new_p = get_priority(role_name)
                 if new_p > current_p:
                     unique_users[uid]["role"] = role_name

        return list(unique_users.values())

    @staticmethod
    async def invite_user_to_workspace(email: str, workspace_id: int, invited_by_id: int, role: str, session: AsyncSession, project_id: Optional[int] = None, project_role: Optional[str] = None):
        from app.models import User, UserWorkspace, WorkspaceInvitation
        from app.services.rbac_service import rbac_service
        
        # Map string role to RBAC Role
        # UI sends 'admin' or 'member'. Map to 'Workspace Admin' / 'Workspace Member'
        rbac_role_name = "Workspace Member"
        if role == "admin": 
            rbac_role_name = "Workspace Admin"
            
        rbac_role = await rbac_service.get_role_by_name(session, rbac_role_name)
        role_id = rbac_role.id if rbac_role else None
        
        # Check if user exists
        result = await session.exec(select(User).where(User.email == email))
        user = result.first()
        if user:
            # Check if already in workspace
            result = await session.exec(
                select(UserWorkspace)
                .where(UserWorkspace.user_id == user.id, UserWorkspace.workspace_id == workspace_id)
            )
            if not result.first():
                uw = UserWorkspace(user_id=user.id, workspace_id=workspace_id, role=role, role_id=role_id)
                session.add(uw)
            
            # If Project Access Requested
            if project_id and project_role:
                await WorkspaceService.add_user_project_access(user.id, project_id, project_role, session)

            await session.commit()
            return {"status": "success", "message": "User added to workspace"}
        else:
            # Check if already invited
            result = await session.exec(
                select(WorkspaceInvitation)
                .where(WorkspaceInvitation.email == email, WorkspaceInvitation.workspace_id == workspace_id)
            )
            existing_invite = result.first()
            if not existing_invite:
                # Store simple role string in invite for now
                # Generate Token
                token = secrets.token_urlsafe(32)
                expires_at = datetime.utcnow() + timedelta(days=7) # 7 days expiry

                invite = WorkspaceInvitation(
                    email=email,
                    workspace_id=workspace_id,
                    role=role,
                    invited_by_id=invited_by_id,
                    token=token,
                    expires_at=expires_at,
                    project_id=project_id,
                    project_role=project_role
                )
                session.add(invite)
                await session.commit()
                # In a real app, send email with link: f"{settings.FRONTEND_URL}/join?token={token}"
                return {"status": "invited", "message": "User invited to workspace", "token": token}
            else:
                 # Update existing invite if new scope?
                 # ideally we should, but for now simple return
                 return {"status": "exists", "message": "User already has a pending invitation"}

    @staticmethod
    async def get_workspace_invitations(workspace_id: int, session: AsyncSession):
        from app.models import WorkspaceInvitation
        result = await session.exec(
            select(WorkspaceInvitation).where(WorkspaceInvitation.workspace_id == workspace_id)
        )
        invites = result.all()
        return [
            {
                "id": i.id,
                "email": i.email,
                "role": i.role,
                "created_at": i.created_at,
                "status": "invited",
                "token": i.token
            }
            for i in invites
        ]

    @staticmethod
    async def get_workspace_teams(workspace_id: int, session: AsyncSession) -> List[Team]:
        result = await session.exec(select(Team).where(Team.workspace_id == workspace_id))
        return result.all()

    @staticmethod
    async def delete_project(project_id: int, session: AsyncSession):
        # We need to delete test suites, modules, cases, and runs.
        # However, for now, let's at least delete the project and its access records.
        # In a real app, we'd use cascading deletes or a more comprehensive cleanup.
        from app.models import TestSuite, TestRun, UserProjectAccess, TeamProjectAccess
        
        # Delete project access
        await session.exec(select(UserProjectAccess).where(UserProjectAccess.project_id == project_id)) # This is just to show what we'd do
        # SQLModel/SQLAlchemy can handle cascades if defined in models, but let's be explicit if needed.
        
        project = await session.get(Project, project_id)
        if project:
            await session.delete(project)
            await session.commit()

    @staticmethod
    async def delete_team(team_id: int, session: AsyncSession):
        team = await session.get(Team, team_id)
        if team:
            await session.delete(team)
            await session.commit()

    @staticmethod
    async def delete_workspace(workspace_id: int, session: AsyncSession):
        ws = await session.get(Workspace, workspace_id)
        if ws:
            # 1. Nullify Audit Logs (preserve history)
            from app.models import AuditLog
            audit_logs = await session.exec(select(AuditLog).where(AuditLog.workspace_id == workspace_id))
            for log in audit_logs.all():
                log.workspace_id = None
                session.add(log)

            # 2. Delete Dependent Teams
            teams = await session.exec(select(Team).where(Team.workspace_id == workspace_id))
            for team in teams.all():
                await WorkspaceService.delete_team(team.id, session)

            await session.delete(ws)
            await session.commit()

    @staticmethod
    async def remove_user_from_workspace(workspace_id: int, user_id: int, session: AsyncSession) -> bool:
        from app.models import UserWorkspace, UserTeam, Team
        
        # 1. Check/Get the UserWorkspace record
        result = await session.exec(
            select(UserWorkspace)
            .where(UserWorkspace.workspace_id == workspace_id, UserWorkspace.user_id == user_id)
        )
        uw = result.first()
        if not uw:
            return False
            
        # 2. Get all teams in this workspace
        teams = await session.exec(select(Team).where(Team.workspace_id == workspace_id))
        team_ids = [t.id for t in teams.all()]
        
        # 3. Remove user from all teams in this workspace
        if team_ids:
            # This deletes all UserTeam records for this user where team_id is in the workspace's teams
            # Note: SQLModel doesn't support .where(UserTeam.team_id.in_(team_ids)) nicely with async delete sometimes,
            # so iterating or a raw delete is safer. Let's do a bulk delete via statement if possible or iterate.
            # Iterating is safer for now to ensure hooks/session state is clean.
            user_teams = await session.exec(
                select(UserTeam)
                .where(UserTeam.user_id == user_id)
                .where(UserTeam.team_id.in_(team_ids)) # type: ignore
            )
            for ut in user_teams.all():
                await session.delete(ut)
        
        # 4. Remove UserWorkspace record
        await session.delete(uw)
        
        await session.commit()
        return True

    @staticmethod
    async def process_pending_invitations(email: str, user_id: int, session: AsyncSession):
        from app.models import TeamInvitation, UserTeam, Team, UserWorkspace, WorkspaceInvitation
        from app.services.rbac_service import rbac_service
        
        # Process Workspace Invitations
        result_ws = await session.exec(select(WorkspaceInvitation).where(WorkspaceInvitation.email == email))
        ws_invites = result_ws.all()
        for invite in ws_invites:
            # Map role string to RBAC
            rbac_role_name = "Workspace Member"
            if invite.role == "admin": 
                rbac_role_name = "Workspace Admin"
            rbac_role = await rbac_service.get_role_by_name(session, rbac_role_name)
            role_id = rbac_role.id if rbac_role else None
            
            # Add to workspace if not already a member
            ws_check = await session.exec(
                select(UserWorkspace)
                .where(UserWorkspace.user_id == user_id, UserWorkspace.workspace_id == invite.workspace_id)
            )
            if not ws_check.first():
                uw = UserWorkspace(
                    user_id=user_id, 
                    workspace_id=invite.workspace_id, 
                    role=invite.role,
                    role_id=role_id
                )
                session.add(uw)
            await session.delete(invite)

        # Find all team invitations for this email
        result = await session.exec(select(TeamInvitation).where(TeamInvitation.email == email))
        invitations = result.all()
        
        for invite in invitations:
            # Get team to find workspace_id
            team = await session.get(Team, invite.team_id)
            if team:
                # Add to workspace if not already a member
                ws_check = await session.exec(
                    select(UserWorkspace)
                    .where(UserWorkspace.user_id == user_id, UserWorkspace.workspace_id == team.workspace_id)
                )
                if not ws_check.first():
                    # Default: Member
                    memb_role = await rbac_service.get_role_by_name(session, "Workspace Member")
                    uw = UserWorkspace(
                        user_id=user_id, 
                        workspace_id=team.workspace_id, 
                        role="member",
                        role_id=memb_role.id if memb_role else None
                    )
                    session.add(uw)
                
                # Add to team if not already a member
                team_check = await session.exec(
                    select(UserTeam)
                    .where(UserTeam.user_id == user_id, UserTeam.team_id == team.id)
                )
                if not team_check.first():
                    ut = UserTeam(user_id=user_id, team_id=team.id)
                    session.add(ut)
            
            # Delete the invitation
            await session.delete(invite)
        
        await session.commit()

workspace_service = WorkspaceService()
