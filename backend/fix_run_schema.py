import asyncio
from sqlalchemy import text
from app.core.database import engine

async def fix_schema():
    print("Fixing 'testrun' schema...")
    async with engine.begin() as conn:
        # suite_name
        try:
            print("Adding 'suite_name'...")
            await conn.execute(text("ALTER TABLE testrun ADD COLUMN IF NOT EXISTS suite_name VARCHAR"))
        except Exception as e:
            print(f"Error adding suite_name: {e}")

        # test_case_name
        try:
            print("Adding 'test_case_name'...")
            await conn.execute(text("ALTER TABLE testrun ADD COLUMN IF NOT EXISTS test_case_name VARCHAR"))
        except Exception as e:
            print(f"Error adding test_case_name: {e}")
            
        # check network_events and execution_log as well, just in case
        try:
            print("Adding 'network_events'...")
            await conn.execute(text("ALTER TABLE testrun ADD COLUMN IF NOT EXISTS network_events JSON"))
        except Exception as e:
            print(f"Error adding network_events: {e}")

        try:
            print("Adding 'execution_log'...")
            await conn.execute(text("ALTER TABLE testrun ADD COLUMN IF NOT EXISTS execution_log JSON"))
        except Exception as e:
            print(f"Error adding execution_log: {e}")

    print("Schema fix execution complete.")

if __name__ == "__main__":
    asyncio.run(fix_schema())
