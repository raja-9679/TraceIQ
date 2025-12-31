import asyncio
import httpx
from app.core.database import get_session_context
from app.core.config import settings
from sqlmodel import select
from app.models import User

# Use localhost for internal testing if needed, or service name
API_URL = "http://localhost:8000/api"

async def verify_rbac_api():
    async with get_session_context() as session:
        # Get a user (e.g. initial user)
        user = (await session.exec(select(User))).first()
        if not user:
            print("No users found to test with.")
            return

        print(f"Testing with user: {user.email}")
        
        # 1. Login to get token
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{API_URL}/auth/login", data={
                "username": user.email,
                "password": "password123" # Assuming default password from seeding or strict environment? 
                # Wait, setup_rbac.py doesn't set passwords. 
                # I should use a user I know the password for, or create one, or bypass auth?
                # Bypassing auth is hard on integration level.
                # verify_user_mgmt.py creates a user with known password.
                # Let's create a temp user here.
            })
            
            # If login fails (unknown password), create a new user to test
            token = None
            if resp.status_code != 200:
                print("Login failed, creating temp user...")
                # Create user directly in DB
                from app.core.auth import get_password_hash
                user.hashed_password = get_password_hash("testpass123")
                session.add(user)
                await session.commit()
                
                resp = await client.post(f"{API_URL}/auth/login", data={
                    "username": user.email,
                    "password": "testpass123"
                })
                if resp.status_code != 200:
                    print(f"Login failed after reset: {resp.text}")
                    return
            
            token = resp.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            
            # 2. Test GET /roles
            print("Testing GET /roles...")
            r_roles = await client.get(f"{API_URL}/roles", headers=headers)
            print(f"Status: {r_roles.status_code}")
            if r_roles.status_code == 200:
                roles = r_roles.json()
                print(f"Roles found: {len(roles)}")
                print(f"Sample Role: {roles[0] if roles else 'None'}")
            else:
                print(f"Error: {r_roles.text}")

            # 3. Test GET /auth/permissions
            print("\nTesting GET /auth/permissions...")
            r_perms = await client.get(f"{API_URL}/auth/permissions", headers=headers)
            print(f"Status: {r_perms.status_code}")
            if r_perms.status_code == 200:
                perms = r_perms.json()
                print(f"System Perms: {len(perms.get('system', []))}")
                print(f"Org Perms Map: {perms.get('organization', {}).keys()}")
                print(f"Project Perms Map: {perms.get('project', {}).keys()}")
            else:
                print(f"Error: {r_perms.text}")

if __name__ == "__main__":
    asyncio.run(verify_rbac_api())
