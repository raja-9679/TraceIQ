import asyncio
import os
import sys

# Ensure we can import from app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import select, delete
from app.core.database import get_session_context as async_session_maker
from app.models import User, Organization, UserOrganization, Team, UserTeam, OrganizationInvitation
from app.services.org_service import org_service

async def verify_user_management():
    print("Starting User Management Verification...")
    
    async with async_session_maker() as session:
        # 1. Setup Data
        print("\n[Step 1] Setting up test data...")
        # Create a test owner
        owner_email = "owner@test.com"
        owner = await session.exec(select(User).where(User.email == owner_email))
        owner = owner.first()
        if not owner:
            owner = User(email=owner_email, full_name="Test Owner", hashed_password="hashed")
            session.add(owner)
            await session.commit()
            print(f"Created Owner: {owner.email}")
        else:
            print(f"Using Owner: {owner.email}")

        # Create a test organization
        org_name = "Test Org Integration"
        org = await session.exec(select(Organization).where(Organization.name == org_name))
        org = org.first()
        if org:
             await org_service.delete_organization(org.id, session)
             print(f"Cleaned up existing org: {org_name}")
        
        org = await org_service.create_organization(org_name, owner.id, session)
        print(f"Created Organization: {org.name} (ID: {org.id})")

        # Create a team
        team = Team(name="Engineering", organization_id=org.id)
        session.add(team)
        await session.commit()
        await session.refresh(team)
        print(f"Created Team: {team.name} (ID: {team.id})")

        # 2. Invite Flow
        print("\n[Step 2] Testing Invite Flow...")
        invite_email = "newuser@test.com"
        # Cleanup if exists
        fail_user = await session.exec(select(User).where(User.email == invite_email))
        fail_user = fail_user.first()
        if fail_user:
            await session.delete(fail_user)
            await session.commit()

        # Invite non-existent user
        res = await org_service.invite_user_to_organization(invite_email, org.id, owner.id, "member", session)
        print(f"Invite Result: {res}")
        assert res["status"] == "invited"
        
        # Verify invitation record
        invites = await org_service.get_org_invitations(org.id, session)
        assert len(invites) == 1
        assert invites[0]["email"] == invite_email
        print("✅ Invitation verified.")

        # 3. Accept Invite / Register User (Simulated)
        print("\n[Step 3] Simulating User Registration & Auto-Join...")
        new_user = User(email=invite_email, full_name="New User", hashed_password="hashed")
        session.add(new_user)
        await session.commit()
        await session.refresh(new_user)
        
        # Manually trigger process_pending_invitations as it would happen on login/signup
        await org_service.process_pending_invitations(invite_email, new_user.id, session)
        
        # Verify membership
        members = await org_service.get_org_members(org.id, session)
        member_emails = [m.email for m in members]
        print(f"Org Members: {member_emails}")
        assert invite_email in member_emails
        print("✅ User successfully added to Org after registration.")

        # 4. Add to Team
        print("\n[Step 4] Testing Team Management...")
        # Add new user to team
        ut = UserTeam(user_id=new_user.id, team_id=team.id)
        session.add(ut)
        await session.commit()
        
        # Verify team members
        team_members = await session.exec(select(UserTeam).where(UserTeam.team_id == team.id))
        assert len(team_members.all()) == 1
        print("✅ User added to Team.")

        # 5. Remove User Flow
        print("\n[Step 5] Testing Remove User Flow...")
        # Remove user from Org
        success = await org_service.remove_user_from_organization(org.id, new_user.id, session)
        assert success is True
        
        # Verify removed from Org
        members = await org_service.get_org_members(org.id, session)
        member_emails = [m.email for m in members]
        assert invite_email not in member_emails
        print("✅ User removed from Organization.")

        # Verify removed from Team (Cascade)
        team_members = await session.exec(select(UserTeam).where(UserTeam.team_id == team.id))
        assert len(team_members.all()) == 0
        print("✅ User automatically removed from Team (Cascade verified).")

        # Cleanup
        print("\n[Cleanup] Deleting test organization...")
        await org_service.delete_organization(org.id, session)

        # Cleanup Audit Logs
        from app.models import AuditLog
        logs = await session.exec(select(AuditLog).where(AuditLog.user_id.in_([owner.id, new_user.id])))
        for log in logs.all():
            await session.delete(log)

        await session.delete(owner)
        await session.delete(new_user)
        await session.commit()
        print("✅ Cleanup complete.")

if __name__ == "__main__":
    asyncio.run(verify_user_management())
