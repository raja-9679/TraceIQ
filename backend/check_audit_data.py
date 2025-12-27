import asyncio
import os
from dotenv import load_dotenv

# Load .env from backend directory
load_dotenv("backend/.env")

from app.core.database import engine
from sqlalchemy import text

async def check_data():
    async with engine.begin() as conn:
        # Check auditlog count
        result = await conn.execute(text("SELECT COUNT(*) FROM auditlog"))
        count = result.scalar()
        print(f"AuditLog count: {count}")
        
        if count > 0:
            result = await conn.execute(text("SELECT * FROM auditlog LIMIT 1"))
            row = result.fetchone()
            print(f"Sample log: {row}")

if __name__ == "__main__":
    asyncio.run(check_data())
