import asyncio
import os
from dotenv import load_dotenv

# Load .env from backend directory
load_dotenv("backend/.env")

from app.core.database import engine
from sqlalchemy import text

async def check_schema():
    async with engine.begin() as conn:
        # Check auditlog table
        result = await conn.execute(text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'auditlog')"))
        exists = result.scalar()
        print(f"Table 'auditlog' exists: {exists}")

        # Check testsuite columns
        result = await conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'testsuite'"))
        columns = [row[0] for row in result.fetchall()]
        print(f"TestSuite columns: {columns}")
        print(f"Has created_by_id: {'created_by_id' in columns}")
        print(f"Has updated_by_id: {'updated_by_id' in columns}")

if __name__ == "__main__":
    asyncio.run(check_schema())
