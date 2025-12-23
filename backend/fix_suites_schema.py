import asyncio
from sqlalchemy import text
from app.core.database import engine

async def fix_schema():
    print("Fixing 'testsuite' schema...")
    async with engine.begin() as conn:
        # 1. execution_mode
        try:
            print("Adding 'execution_mode'...")
            await conn.execute(text("ALTER TABLE testsuite ADD COLUMN IF NOT EXISTS execution_mode VARCHAR DEFAULT 'continuous'"))
        except Exception as e:
            print(f"Error adding execution_mode: {e}")

        # 2. parent_id
        try:
            print("Adding 'parent_id'...")
            await conn.execute(text("ALTER TABLE testsuite ADD COLUMN IF NOT EXISTS parent_id INTEGER REFERENCES testsuite(id)"))
        except Exception as e:
            print(f"Error adding parent_id: {e}")

        # 3. settings
        try:
            print("Adding 'settings'...")
            # JSON type support varies by DB, but Postgres supports JSON
            await conn.execute(text("ALTER TABLE testsuite ADD COLUMN IF NOT EXISTS settings JSON"))
        except Exception as e:
            print(f"Error adding settings: {e}")

        # 4. inherit_settings
        try:
            print("Adding 'inherit_settings'...")
            await conn.execute(text("ALTER TABLE testsuite ADD COLUMN IF NOT EXISTS inherit_settings BOOLEAN DEFAULT TRUE"))
        except Exception as e:
            print(f"Error adding inherit_settings: {e}")

    print("Schema fix execution complete.")

if __name__ == "__main__":
    asyncio.run(fix_schema())
