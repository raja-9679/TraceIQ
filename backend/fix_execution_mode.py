import asyncio
from sqlalchemy import text
from app.core.database import engine

async def fix_execution_mode():
    print("Fixing execution_mode enum type and values...")
    async with engine.begin() as conn:
        try:
            # 1. Rename old type
            print("Renaming old enum type...")
            await conn.execute(text("ALTER TYPE executionmode RENAME TO executionmode_old"))
            
            # 2. Create new type
            print("Creating new enum type...")
            await conn.execute(text("CREATE TYPE executionmode AS ENUM ('continuous', 'separate')"))
            
            # 3. Alter table to use new type with conversion
            print("Migrating table data to new enum type...")
            await conn.execute(text("""
                ALTER TABLE testsuite 
                ALTER COLUMN execution_mode TYPE executionmode 
                USING lower(execution_mode::text)::executionmode
            """))
            
            # 4. Drop old type
            print("Dropping old enum type...")
            await conn.execute(text("DROP TYPE executionmode_old"))
            
            print("Successfully updated execution_mode enum.")
        except Exception as e:
            print(f"Error updating execution_mode: {e}")
            # If something fails, we might be in a weird state, but the transaction should roll back.

if __name__ == "__main__":
    asyncio.run(fix_execution_mode())
