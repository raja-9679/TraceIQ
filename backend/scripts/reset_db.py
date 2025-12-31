import asyncio
import sys
import os
from sqlalchemy import text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import get_session_context

async def reset_database():
    print("WARNING: Wiping all data from database...")
    async with get_session_context() as session:
        # Tables to truncate
        # We need to respect foreign keys, so CASCADE is essential.
        # We truncate the root tables and let it cascade.
        try:
            # Order matters less with CASCADE but let's be thorough
            # Truncating Tenant, User, Role, Permission should hit everything via FKs
            # But let's verify if CASCADE is set up in DB. Usually TRUNCATE table CASCADE works regardless of ON DELETE constraints.
            
            tables = [
                "users",
                "tenant",
                "organization",
                "project",
                "team",
                "role",
                "permission",
                # Join tables might not be cleared if they don't have FKs or if we miss them?
                # Best to list them or use a generic "TRUNCATE all tables" approach.
                "userorganization",
                "usersystemrole",
                "userteam",
                "userprojectaccess",
                "teamprojectaccess",
                "rolepermission",
                "testsuite",
                "testcase",
                "testrun",
                "testcaseresult",
                "auditlog",
                "organizationinvitation",
                "teaminvitation"
            ]
            
            # Construct single truncate statement for efficiency/integrity
            stmt = f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE;"
            await session.exec(text(stmt))
            await session.commit()
            print("✅ Database cleared successfully.")
            
        except Exception as e:
            print(f"❌ Error during reset: {e}")
            # Fallback: Try individually if bulk fails? No, bulk is better.
            
if __name__ == "__main__":
    # Safety check
    confirm = os.environ.get("CONFIRM_RESET", "no")
    if confirm != "yes":
        print("To reset DB, run with CONFIRM_RESET=yes")
        sys.exit(1)
        
    asyncio.run(reset_database())
