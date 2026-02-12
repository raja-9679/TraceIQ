import asyncio
import os
import sys
from sqlalchemy import text

# Ensure we can import from app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import get_session_context

async def migrate_execution_mode():
    """
    Migration script to add 'parallel' value to executionmode enum.
    
    This migration adds support for parallel test execution by extending
    the executionmode enum type in PostgreSQL.
    """
    print("Starting Migration: Add 'parallel' to executionmode enum...")
    
    async with get_session_context() as session:
        try:
            # Check if 'parallel' already exists
            result = await session.exec(text(
                "SELECT EXISTS (SELECT 1 FROM pg_enum WHERE enumlabel = 'parallel' "
                "AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'executionmode'));"
            ))
            exists = result.one()
            
            if exists:
                print("   ℹ 'parallel' value already exists in executionmode enum. Skipping.")
            else:
                # Add 'parallel' to the enum
                await session.exec(text("ALTER TYPE executionmode ADD VALUE 'parallel';"))
                await session.commit()
                print("   ✅ Added 'parallel' value to executionmode enum")
                print("   ⚠️  Note: Backend services need to be restarted to recognize the new enum value")
        except Exception as e:
            print(f"   ❌ Error during migration: {e}")
            await session.rollback()
            raise
    
    print("✅ Migration Complete.")

if __name__ == "__main__":
    asyncio.run(migrate_execution_mode())
