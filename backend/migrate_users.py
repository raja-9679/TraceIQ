import asyncio
import os
from dotenv import load_dotenv

# Load .env from backend directory
load_dotenv("backend/.env")

from app.core.database import engine
from sqlalchemy import text

async def migrate():
    async with engine.begin() as conn:
        # Add user_id column to testrun table
        await conn.execute(text("ALTER TABLE testrun ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id)"))
        print("Column 'user_id' added successfully to 'testrun' table.")

if __name__ == "__main__":
    asyncio.run(migrate())
