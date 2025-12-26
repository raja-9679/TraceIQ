import asyncio
from sqlalchemy import text
from app.core.database import engine

async def fix_schema():
    print("Adding 'device' column to 'testrun' table...")
    async with engine.begin() as conn:
        try:
            print("Adding 'device' column (optional for mobile device emulation)...")
            await conn.execute(text("ALTER TABLE testrun ADD COLUMN IF NOT EXISTS device VARCHAR"))
            print("Successfully added 'device' column.")
        except Exception as e:
            print(f"Error adding device column: {e}")

    print("Schema fix complete.")

if __name__ == "__main__":
    asyncio.run(fix_schema())
