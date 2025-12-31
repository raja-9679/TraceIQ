from typing import Optional, List
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from app.models import Organization, UserOrganization, User, Team, UserTeam, Project, AuditLog, UserProjectAccess
from datetime import datetime, timedelta
import secrets

class OrgService:
    @staticmethod
    async def create_organization(
        name: str, 
        owner_id: int, 
        session: AsyncSession, 
        description: Optional[str] = None, 
        commit: bool = True, 
        auto_create_project: bool = False,
        project_name: Optional[str] = None,
        tenant_id: Optional[int] = None
    ) -> Organization:
        from app.services.rbac_service import rbac_service
        
        org = Organization(name=name, description=description, tenant_id=tenant_id)
        session.add(org)
        await session.flush()
        
        # Link owner as Org Admin
        admin_role = await rbac_service.get_role_by_name(session, "Organization Admin")
        if not admin_role:
             raise Exception("System Role 'Organization Admin' not found. Run setup_rbac.py")
             
        user_org = UserOrganization(user_id=owner_id, organization_id=org.id, role_id=admin_role.id, role="admin")
        session.add(user_org)
        
        # Audit log
        audit = AuditLog(
            entity_type="org",
            entity_id=org.id,
            action="create",
            user_id=owner_id,
            organization_id=org.id,
            changes={"name": name}
        )
        session.add(audit)
        
        if auto_create_project:
            # Create a default project
            project = Project(
                name=project_name or "Initial Project",
                description="Your first project",
                organization_id=org.id
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
            await session.refresh(org)
        else:
            await session.flush()
        return org

    @staticmethod
    async def get_user_organizations(user_id: int, session: AsyncSession) -> List[Organization]:
        result = await session.exec(
            select(Organization)
            .join(UserOrganization)
            .where(UserOrganization.user_id == user_id)
        )
        return result.all()

    @staticmethod
    async def create_project(name: str, org_id: int, creator_id: int, session: AsyncSession, description: Optional[str] = None, commit: bool = True) -> Project:
        from app.services.rbac_service import rbac_service
        project = Project(name=name, description=description, organization_id=org_id)
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
            organization_id=org_id,
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
    async def get_org_members(org_id: int, session: AsyncSession) -> List[User]:
        from app.models import User, UserOrganization
        result = await session.exec(
            select(User)
            .join(UserOrganization)
            .where(UserOrganization.organization_id == org_id)
        )
        return result.all()

    @staticmethod
    async def get_org_members_detailed(org_id: int, session: AsyncSession, viewer_id: int):
        from app.models import User, UserOrganization, Role, UserProjectAccess
        from app.services.rbac_service import rbac_service
        
        # 1. Determine Viewer's Scope
        # Check if Tenant Admin or Org Admin
        is_tenant_admin = await rbac_service.has_permission(session, viewer_id, "tenant:manage_settings") # Proxy for Tenant Admin
        is_org_admin = await rbac_service.has_permission(session, viewer_id, "org:manage_users", org_id=org_id)
        
        if is_tenant_admin or is_org_admin:
            # Full Access: Return all org members
            stmt = (
                select(User, UserOrganization.role, Role.name)
                .join(UserOrganization, UserOrganization.user_id == User.id)
                .outerjoin(Role, UserOrganization.role_id == Role.id)
                .where(UserOrganization.organization_id == org_id)
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
                select(User, UserOrganization.role, Role.name)
                .join(UserOrganization, UserOrganization.user_id == User.id)
                .outerjoin(Role, UserOrganization.role_id == Role.id)
                .join(UserProjectAccess, UserProjectAccess.user_id == User.id)
                .where(UserOrganization.organization_id == org_id)
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
        from app.models import User, UserOrganization, Organization, Role
        
        if not tenant_ids:
            return []
            
        stmt = (
            select(User, UserOrganization.role, Role.name, Organization.name)
            .join(UserOrganization, UserOrganization.user_id == User.id)
            .join(Organization, UserOrganization.organization_id == Organization.id)
            .outerjoin(Role, UserOrganization.role_id == Role.id)
            .where(Organization.tenant_id.in_(tenant_ids)) # type: ignore
        )
        
        result = await session.exec(stmt)
        members = result.all()
        
        # Flatten and formatting
        # A user might be in multiple orgs. Tenant Admin wants to see "User List".
        # Should we show duplicates or merge?
        # User list "in all org". Implies listed per org or just unique users?
        # "view all the user in all org".
        # Usually an Admin User Table is unique users.
        # But for "assign to specific org" flow, we need to know who is where?
        # Let's return unique users but maybe annotate with orgs?
        # Or just return plain list for now as per `UserRead` model (simplified).
        # Actually `DetailedMember` expects simplified fields. 
        # Let's return unique users for the main table.
        
        unique_users = {}
        for m in members:
            uid = m[0].id
            if uid not in unique_users:
                unique_users[uid] = {
                    "id": uid,
                    "full_name": m[0].full_name,
                    "email": m[0].email,
                    "role": m[2] if m[2] else m[1], # Shows role in FIRST found org. imperfect but fits MVP
                    "last_login_at": m[0].last_login_at,
                    "is_active": m[0].is_active,
                    "status": "active",
                    # Add org info?
                    "organization": m[3]
                }
        
        return list(unique_users.values())

    @staticmethod
    async def invite_user_to_organization(email: str, org_id: int, invited_by_id: int, role: str, session: AsyncSession, project_id: Optional[int] = None, project_role: Optional[str] = None):
        from app.models import User, UserOrganization, OrganizationInvitation
        from app.services.rbac_service import rbac_service
        
        # Map string role to RBAC Role
        # UI sends 'admin' or 'member'. Map to 'Organization Admin' / 'Organization Member'
        rbac_role_name = "Organization Member"
        if role == "admin": 
            rbac_role_name = "Organization Admin"
            
        rbac_role = await rbac_service.get_role_by_name(session, rbac_role_name)
        role_id = rbac_role.id if rbac_role else None
        
        # Check if user exists
        result = await session.exec(select(User).where(User.email == email))
        user = result.first()
        if user:
            # Check if already in org
            result = await session.exec(
                select(UserOrganization)
                .where(UserOrganization.user_id == user.id, UserOrganization.organization_id == org_id)
            )
            if not result.first():
                uo = UserOrganization(user_id=user.id, organization_id=org_id, role=role, role_id=role_id)
                session.add(uo)
            
            # If Project Access Requested
            if project_id and project_role:
                await OrgService.add_user_project_access(user.id, project_id, project_role, session)

            await session.commit()
            return {"status": "success", "message": "User added to organization"}
        else:
            # Check if already invited
            result = await session.exec(
                select(OrganizationInvitation)
                .where(OrganizationInvitation.email == email, OrganizationInvitation.organization_id == org_id)
            )
            existing_invite = result.first()
            if not existing_invite:
                # Store simple role string in invite for now
                # Generate Token
                token = secrets.token_urlsafe(32)
                expires_at = datetime.utcnow() + timedelta(days=7) # 7 days expiry

                invite = OrganizationInvitation(
                    email=email,
                    organization_id=org_id,
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
                return {"status": "invited", "message": "User invited to organization", "token": token}
            else:
                 # Update existing invite if new scope?
                 # ideally we should, but for now simple return
                 return {"status": "exists", "message": "User already has a pending invitation"}

    @staticmethod
    async def get_org_invitations(org_id: int, session: AsyncSession):
        from app.models import OrganizationInvitation
        result = await session.exec(
            select(OrganizationInvitation).where(OrganizationInvitation.organization_id == org_id)
        )
        invites = result.all()
        return [
            {
                "id": i.id,
                "email": i.email,
                "role": i.role,
                "created_at": i.created_at,
                "status": "invited"
            }
            for i in invites
        ]

    @staticmethod
    async def get_org_teams(org_id: int, session: AsyncSession) -> List[Team]:
        result = await session.exec(select(Team).where(Team.organization_id == org_id))
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
    async def delete_organization(org_id: int, session: AsyncSession):
        org = await session.get(Organization, org_id)
        if org:
            # 1. Nullify Audit Logs (preserve history)
            from app.models import AuditLog
            audit_logs = await session.exec(select(AuditLog).where(AuditLog.organization_id == org_id))
            for log in audit_logs.all():
                log.organization_id = None
                session.add(log)

            # 2. Delete Dependent Teams
            teams = await session.exec(select(Team).where(Team.organization_id == org_id))
            for team in teams.all():
                await OrgService.delete_team(team.id, session)

            await session.delete(org)
            await session.commit()

    @staticmethod
    async def remove_user_from_organization(org_id: int, user_id: int, session: AsyncSession) -> bool:
        from app.models import UserOrganization, UserTeam, Team
        
        # 1. Check/Get the UserOrganization record
        result = await session.exec(
            select(UserOrganization)
            .where(UserOrganization.organization_id == org_id, UserOrganization.user_id == user_id)
        )
        uo = result.first()
        if not uo:
            return False
            
        # 2. Get all teams in this organization
        teams = await session.exec(select(Team).where(Team.organization_id == org_id))
        team_ids = [t.id for t in teams.all()]
        
        # 3. Remove user from all teams in this organization
        if team_ids:
            # This deletes all UserTeam records for this user where team_id is in the org's teams
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
        
        # 4. Remove UserOrganization record
        await session.delete(uo)
        
        await session.commit()
        return True

    @staticmethod
    async def process_pending_invitations(email: str, user_id: int, session: AsyncSession):
        from app.models import TeamInvitation, UserTeam, Team, UserOrganization, OrganizationInvitation
        from app.services.rbac_service import rbac_service
        
        # Process Org Invitations
        result_org = await session.exec(select(OrganizationInvitation).where(OrganizationInvitation.email == email))
        org_invites = result_org.all()
        for invite in org_invites:
            # Map role string to RBAC
            rbac_role_name = "Organization Member"
            if invite.role == "admin": 
                rbac_role_name = "Organization Admin"
            rbac_role = await rbac_service.get_role_by_name(session, rbac_role_name)
            role_id = rbac_role.id if rbac_role else None
            
            # Add to organization if not already a member
            org_check = await session.exec(
                select(UserOrganization)
                .where(UserOrganization.user_id == user_id, UserOrganization.organization_id == invite.organization_id)
            )
            if not org_check.first():
                uo = UserOrganization(
                    user_id=user_id, 
                    organization_id=invite.organization_id, 
                    role=invite.role,
                    role_id=role_id
                )
                session.add(uo)
            await session.delete(invite)

        # Find all team invitations for this email
        result = await session.exec(select(TeamInvitation).where(TeamInvitation.email == email))
        invitations = result.all()
        
        for invite in invitations:
            # Get team to find organization_id
            team = await session.get(Team, invite.team_id)
            if team:
                # Add to organization if not already a member
                org_check = await session.exec(
                    select(UserOrganization)
                    .where(UserOrganization.user_id == user_id, UserOrganization.organization_id == team.organization_id)
                )
                if not org_check.first():
                    # Default: Member
                    memb_role = await rbac_service.get_role_by_name(session, "Organization Member")
                    uo = UserOrganization(
                        user_id=user_id, 
                        organization_id=team.organization_id, 
                        role="member",
                        role_id=memb_role.id if memb_role else None
                    )
                    session.add(uo)
                
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

org_service = OrgService()
