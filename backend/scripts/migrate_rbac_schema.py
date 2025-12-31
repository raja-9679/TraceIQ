import asyncio
import os
import sys
from sqlalchemy import text

# Ensure we can import from app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import get_session_context, init_db

async def migrate_schema():
    print("Starting Schema Migration for RBAC...")
    
    # 1. Ensure new tables (Roles, Permissions, UserSystemRole) are created
    print(" - Creating new tables (Role, Permission, UserSystemRole)...")
    await init_db() 
    
    # 2. Add columns to existing tables
    print(" - Adding 'role_id' columns to existing tables...")
    async with get_session_context() as session:
        # UserOrganization
        try:
            await session.exec(text("ALTER TABLE userorganization ADD COLUMN IF NOT EXISTS role_id INTEGER REFERENCES role(id)"))
            print("   + Added role_id to userorganization")
        except Exception as e:
            print(f"   ! Error altering userorganization: {e}")

        # UserProjectAccess
        try:
            await session.exec(text("ALTER TABLE userprojectaccess ADD COLUMN IF NOT EXISTS role_id INTEGER REFERENCES role(id)"))
            print("   + Added role_id to userprojectaccess")
        except Exception as e:
            print(f"   ! Error altering userprojectaccess: {e}")

        # TeamProjectAccess
        try:
            await session.exec(text("ALTER TABLE teamprojectaccess ADD COLUMN IF NOT EXISTS role_id INTEGER REFERENCES role(id)"))
            print("   + Added role_id to teamprojectaccess")
        except Exception as e:
            print(f"   ! Error altering teamprojectaccess: {e}")
            
        await session.commit()
    
    print("✅ Schema Migration Complete.")

if __name__ == "__main__":
    asyncio.run(migrate_schema())
