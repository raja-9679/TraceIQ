import asyncio
from app.core.database import engine
from sqlalchemy import text

async def migrate():
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE testcaseresult ADD COLUMN IF NOT EXISTS request_params JSONB DEFAULT '{}'::jsonb"))
        print("Column 'request_params' added successfully to 'testcaseresult' table.")

if __name__ == "__main__":
    asyncio.run(migrate())
