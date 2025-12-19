import asyncio
from sqlalchemy import text
from app.core.database import engine

async def fix_schema_network():
    print("Checking database schema for network_events...")
    async with engine.begin() as conn:
        try:
            await conn.execute(text("ALTER TABLE testrun ADD COLUMN IF NOT EXISTS network_events JSON"))
            print("Ensured column 'network_events' exists.")
        except Exception as e:
            print(f"Error adding column 'network_events': {e}")

if __name__ == "__main__":
    asyncio.run(fix_schema_network())
