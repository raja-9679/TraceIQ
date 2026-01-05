import asyncio
import httpx
from app.core.database import get_session_context
from app.core.config import settings
from sqlmodel import select
from app.models import User, Project, Team, Role, UserProjectAccess, UserSystemRole
from app.core.auth import get_password_hash

API_URL = "http://localhost:8000/api"

async def verify_execution_logic():
    print("Starting verification of Execution Mode Logic...")
    async with get_session_context() as session:
        # 1. Setup User and Login
        user = (await session.exec(select(User).where(User.email == "admin@traceiq.io"))).first()
        if not user:
            # Fallback to any user
            user = (await session.exec(select(User))).first()
        
        if not user:
            print("No users found. Creating one.")
            user = User(
                email="test_exec_logic@example.com", 
                full_name="Test Exec Logic", 
                hashed_password=get_password_hash("password123"),
                is_active=True
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        else:
            # Ensure password is known
            user.hashed_password = get_password_hash("password123")
            session.add(user)
            await session.commit()

        print(f"Using user: {user.email}")

        async with httpx.AsyncClient() as client:
            # Login
            resp = await client.post(f"{API_URL}/auth/login", data={
                "username": user.email,
                "password": "password123"
            })
            if resp.status_code != 200:
                print(f"Login failed: {resp.text}")
                return
            
            token = resp.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            # 2. Create a dedicated Project for this test to ensure permissions
            from app.models import Workspace
            ws = (await session.exec(select(Workspace))).first()
            if not ws:
                print("No workspace found. Aborting.")
                return
            
            project = Project(
                name="Exec Logic Test Project " + user.email, 
                workspace_id=ws.id, 
                created_by_id=user.id
            )
            session.add(project)
            # Give user admin access to project (simulating creator access or explicit grant)
            # Assuming creator gets access via other means or we need to add UserProjectAccess
            await session.commit()
            await session.refresh(project)

            # Explicitly grant access
            from app.models import UserProjectAccess, Role
            # Find Admin role
            admin_role = (await session.exec(select(Role).where(Role.name == "Admin"))).first()
            if admin_role:
                 # Check if access exists
                access = (await session.exec(select(UserProjectAccess).where(UserProjectAccess.user_id == user.id, UserProjectAccess.project_id == project.id))).first()
                if not access:
                    access = UserProjectAccess(user_id=user.id, project_id=project.id, role_id=admin_role.id)
                    session.add(access)
                    await session.commit()
            
            # Debug: Check permissions
            from app.services.rbac_service import rbac_service
            has_perm = await rbac_service.has_permission(session, user.id, "project:create_suite", project_id=project.id)
            print(f"Debug: User ID {user.id} has permission 'project:create_suite' on Project {project.id}? {has_perm}")
            
            sys_roles = await session.exec(select(UserSystemRole).where(UserSystemRole.user_id == user.id))
            print(f"Debug: User System Roles: {[r.role_id for r in sys_roles.all()]}")
            
            proj_access = await session.exec(select(UserProjectAccess).where(UserProjectAccess.user_id == user.id, UserProjectAccess.project_id == project.id))
            print(f"Debug: User Project Access: {[a.role_id for a in proj_access.all()]}")

            print(f"Using Project: {project.name} (ID: {project.id})")

            # 3. Create Parent Suite (Continuous)
            print("\nCreating Parent Suite (Continuous)...")
            parent_payload = {
                "name": "Verify Logic Parent",
                "description": "Parent suite for verification",
                "project_id": project.id,
                "execution_mode": "continuous"
            }
            resp = await client.post(f"{API_URL}/suites", json=parent_payload, headers=headers)
            if resp.status_code != 200:
                print(f"Failed to create parent suite: {resp.text}")
                # Try to clean up if it already exists from previous failed run
                return
            
            parent_suite = resp.json()
            parent_id = parent_suite["id"]
            print(f"Parent Suite Created: ID {parent_id}, Mode: {parent_suite['execution_mode']}")
            assert parent_suite['execution_mode'] == 'continuous'

            # 4. Create Child Suite
            print("\nCreating Child Suite...")
            child_payload = {
                "name": "Verify Logic Sub",
                "project_id": project.id,
                "parent_id": parent_id,
                "execution_mode": "continuous"
            }
            resp = await client.post(f"{API_URL}/suites", json=child_payload, headers=headers)
            if resp.status_code != 200:
                print(f"Failed to create child suite: {resp.text}")
                return
            
            child_suite = resp.json()
            print(f"Child Suite Created: ID {child_suite['id']}")

            # 5. Verify Parent Execution Mode Changed
            print("\nVerifying Parent Execution Mode Update...")
            resp = await client.get(f"{API_URL}/suites/{parent_id}", headers=headers)
            updated_parent = resp.json()
            print(f"Parent Mode after adding child: {updated_parent['execution_mode']}")
            
            if updated_parent['execution_mode'] == 'separate':
                print("SUCCESS: Parent mode automatically updated to 'separate'.")
            else:
                print(f"FAILURE: Parent mode did not update. Got {updated_parent['execution_mode']}")

            # 6. Try to update Parent back to Continuous
            print("\nAttempting to set Parent back to Continuous (Should Fail)...")
            update_payload = {
                "execution_mode": "continuous"
            }
            resp = await client.put(f"{API_URL}/suites/{parent_id}", json=update_payload, headers=headers)
            
            if resp.status_code == 400:
                print(f"SUCCESS: Update rejected as expected. Error: {resp.json()['detail']}")
            else:
                print(f"FAILURE: Update succeeded or failed with unexpected code. Status: {resp.status_code}, Response: {resp.text}")

            # Cleanup
            print("\nCleaning up...")
            if 'parent_id' in locals():
                await client.delete(f"{API_URL}/suites/{parent_id}", headers=headers)
            
            # Delete project
            # await client.delete(f"{API_URL}/projects/{project.id}", headers=headers) 
            # We might not have endpoint for deleting project in this script, doing via DB/cleanup not strictly necessary for ephemeral test but good practice.
            # Just leaving it for now.
            print("Cleanup complete.")

if __name__ == "__main__":
    asyncio.run(verify_execution_logic())
