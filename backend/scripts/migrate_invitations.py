import asyncio
import os
import sys

# Add backend directory
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.database import get_session_context
from sqlalchemy import text

async def migrate():
    print("Starting Migration: Update OrganizationInvitation table...")
    async with get_session_context() as session:
        # Add project_id column
        print("Adding 'project_id' column...")
        try:
            await session.exec(text("ALTER TABLE organizationinvitation ADD COLUMN project_id INTEGER NULL"))
            print("  - Added 'project_id'")
        except Exception as e:
            print(f"  - Error (maybe exists): {e}")

        # Add project_role column
        print("Adding 'project_role' column...")
        try:
            await session.exec(text("ALTER TABLE organizationinvitation ADD COLUMN project_role VARCHAR NULL"))
            print("  - Added 'project_role'")
        except Exception as e:
            print(f"  - Error (maybe exists): {e}")
            
        await session.commit()
        print("Migration Complete.")

if __name__ == "__main__":
    asyncio.run(migrate())
