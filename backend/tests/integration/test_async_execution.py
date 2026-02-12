import asyncio
import httpx
import sys
import os
import time

# Ensure we can import app
sys.path.append(os.getcwd())

from app.core.database import get_session_context
from app.core.auth import get_password_hash
from sqlmodel import select
from app.models import User, Project, Workspace, UserProjectAccess, Role, TestSuite, TestCase

API_URL = "http://localhost:8000/api"

async def verify_async_execution():
    print("Starting E2E Verification of Async Execution...")
    
    async with get_session_context() as session:
        # 1. Setup User (Admin)
        # 1. Setup User
        user = (await session.exec(select(User).where(User.email == "verify_async@traceiq.io"))).first()
        
        if not user:
             print("Creating verify_async user...")
             user = User(email="verify_async@traceiq.io", full_name="Verifier", hashed_password=get_password_hash("password"))
             session.add(user)
             await session.commit()
             await session.refresh(user)
        else:
             print("Using existing verify_async user.")
        
        # 2. Login
        async with httpx.AsyncClient() as client:
            print(f"Logging in as {user.email}...")
            # Note: Assuming default password 'password' for test user or 'password123' if from previous script
            # We'll try to login, if fail, we might need manual help. 
            # Actually, let's just create a fresh user or force password update.
            user.hashed_password = get_password_hash("password")
            session.add(user)
            await session.commit()
            
            resp = await client.post(f"{API_URL}/auth/login", data={"username": user.email, "password": "password"})
            if resp.status_code != 200:
                print(f"Login Failed: {resp.text}")
                return
            
            token = resp.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            
            # 3. Setup Project & Suite
            ws = (await session.exec(select(Workspace))).first()
            if not ws:
                print("No workspace found. Please init DB.")
                return

            project_name = f"Async Verify Project {int(time.time())}"
            project = Project(name=project_name, workspace_id=ws.id, created_by_id=user.id)
            session.add(project)
            await session.commit()
            await session.refresh(project)
            
            # Grant Access
            admin_role = (await session.exec(select(Role).where(Role.name == "Admin"))).first()
            if not admin_role:
                 print("Admin role not found! Fetching any role...")
                 admin_role = (await session.exec(select(Role))).first()
            
            if admin_role:
                 print(f"Granting role {admin_role.name} ({admin_role.id})")
                 access = UserProjectAccess(user_id=user.id, project_id=project.id, role_id=admin_role.id)
                 session.add(access)
                 await session.commit()
            else:
                 print("No roles found in DB. Cannot grant access.")
            
            # Create Suite
            suite_payload = {"name": "Async Test Suite", "project_id": project.id, "execution_mode": "continuous"}
            resp = await client.post(f"{API_URL}/suites", json=suite_payload, headers=headers)
            if resp.status_code not in [200, 201]:
                print(f"Create Suite Failed: {resp.status_code} {resp.text}")
                return
            suite_id = resp.json()["id"]

            
            # Create Case
            case_payload = {
                "name": "Quick Test",
                "steps": [{"id": "1", "type": "goto", "value": "about:blank"}]
            }
            c_resp = await client.post(f"{API_URL}/suites/{suite_id}/cases", json=case_payload, headers=headers)

            if c_resp.status_code not in [200, 201]:
                 print(f"Create Case Failed: {c_resp.status_code} {c_resp.text}")
                 return

            
            print(f"Created Suite {suite_id} with 1 test case.")
            
            # 4. Trigger Run
            print("Triggering Run via API...")
            # Note: /runs takes query params for suite_id
            run_resp = await client.post(f"{API_URL}/runs", params={"suite_id": suite_id, "browser": "chromium"}, headers=headers)
            
            if run_resp.status_code != 200:
                print(f"Failed to trigger run: {run_resp.text}")
                return
                
            run_json = run_resp.json()
            run_id = run_json[0]["id"]
            print(f"Run {run_id} created. Initial status: {run_json[0]['status']}")
            
            # 5. Poll for Completion
            print("Polling for completion (Expect Async Callback)...")
            start_time = time.time()
            final_status = None
            
            for _ in range(30):
                resp = await client.get(f"{API_URL}/runs/{run_id}", headers=headers)
                data = resp.json()
                status = data["status"]
                print(f"[{int(time.time()-start_time)}s] Status: {status}")
                
                if status in ["passed", "failed", "error"]:
                    final_status = status
                    print(f"\nRun Completed! Final Status: {status}")
                    print(f"Duration: {data.get('duration_ms')}ms")
                    print(f"Video URL: {data.get('video_url')}")
                    assert status == "passed", f"Test failed with error: {data.get('error_message')}"
                    print("VERIFICATION SUCCESS: Async flow execution confirmed.")
                    break
                
                await asyncio.sleep(1)
                
            if not final_status:
                print("TIMEOUT: Run did not complete in 30 seconds.")
                print("Suggestion: Check if Celery worker was restarted and execution-engine is running.")

if __name__ == "__main__":
    asyncio.run(verify_async_execution())
