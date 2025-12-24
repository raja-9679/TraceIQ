import asyncio
from sqlalchemy import text
from app.core.database import engine

async def fix_schema():
    print("Adding 'browser' column to 'testrun' table...")
    async with engine.begin() as conn:
        try:
            print("Adding 'browser' column with default value 'chromium'...")
            await conn.execute(text("ALTER TABLE testrun ADD COLUMN IF NOT EXISTS browser VARCHAR DEFAULT 'chromium'"))
            print("Successfully added 'browser' column.")
        except Exception as e:
            print(f"Error adding browser column: {e}")

    print("Schema fix complete.")

if __name__ == "__main__":
    asyncio.run(fix_schema())
