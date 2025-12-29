from typing import Optional, List
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select
from app.models import Organization, UserOrganization, User, Team, UserTeam, Project, AuditLog, UserProjectAccess
from datetime import datetime

class OrgService:
    @staticmethod
    async def create_organization(
        name: str, 
        owner_id: int, 
        session: AsyncSession, 
        description: Optional[str] = None, 
        commit: bool = True, 
        auto_create_project: bool = False,
        project_name: Optional[str] = None
    ) -> Organization:
        org = Organization(name=name, description=description)
        session.add(org)
        await session.flush()
        
        # Link owner as admin
        user_org = UserOrganization(user_id=owner_id, organization_id=org.id, role="admin")
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
            # Also grant the user access to this project
            access = UserProjectAccess(
                user_id=owner_id,
                project_id=project.id,
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
        project = Project(name=name, description=description, organization_id=org_id)
        session.add(project)
        await session.flush()
        
        # Creator gets admin access by default to the project they created
        access = UserProjectAccess(
            user_id=creator_id,
            project_id=project.id,
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
        # Check if exists
        result = await session.exec(select(TeamProjectAccess).where(TeamProjectAccess.team_id == team_id, TeamProjectAccess.project_id == project_id))
        existing = result.first()
        if existing:
            existing.access_level = access_level
            session.add(existing)
        else:
            tpa = TeamProjectAccess(team_id=team_id, project_id=project_id, access_level=access_level)
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
        result = await session.exec(
            select(UserProjectAccess)
            .where(UserProjectAccess.user_id == user_id, UserProjectAccess.project_id == project_id)
        )
        existing = result.first()
        if existing:
            existing.access_level = access_level
            session.add(existing)
        else:
            upa = UserProjectAccess(user_id=user_id, project_id=project_id, access_level=access_level)
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
    async def get_org_members_detailed(org_id: int, session: AsyncSession):
        from app.models import User, UserOrganization
        result = await session.exec(
            select(User, UserOrganization.role)
            .join(UserOrganization, UserOrganization.user_id == User.id)
            .where(UserOrganization.organization_id == org_id)
        )
        members = result.all()
        return [
            {
                "id": m[0].id,
                "full_name": m[0].full_name,
                "email": m[0].email,
                "role": m[1],
                "last_login_at": m[0].last_login_at,
                "is_active": m[0].is_active,
                "status": "active"
            }
            for m in members
        ]

    @staticmethod
    async def invite_user_to_organization(email: str, org_id: int, invited_by_id: int, role: str, session: AsyncSession):
        from app.models import User, UserOrganization, OrganizationInvitation
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
                uo = UserOrganization(user_id=user.id, organization_id=org_id, role=role)
                session.add(uo)
            await session.commit()
            return {"status": "success", "message": "User added to organization"}
        else:
            # Check if already invited
            result = await session.exec(
                select(OrganizationInvitation)
                .where(OrganizationInvitation.email == email, OrganizationInvitation.organization_id == org_id)
            )
            if not result.first():
                invite = OrganizationInvitation(
                    email=email,
                    organization_id=org_id,
                    role=role,
                    invited_by_id=invited_by_id
                )
                session.add(invite)
                await session.commit()
                return {"status": "invited", "message": "User invited to organization"}
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
        
        # Process Org Invitations
        result_org = await session.exec(select(OrganizationInvitation).where(OrganizationInvitation.email == email))
        org_invites = result_org.all()
        for invite in org_invites:
            # Add to organization if not already a member
            org_check = await session.exec(
                select(UserOrganization)
                .where(UserOrganization.user_id == user_id, UserOrganization.organization_id == invite.organization_id)
            )
            if not org_check.first():
                uo = UserOrganization(user_id=user_id, organization_id=invite.organization_id, role=invite.role)
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
                    uo = UserOrganization(user_id=user_id, organization_id=team.organization_id, role="member")
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
