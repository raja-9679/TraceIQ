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
            result = await conn.execute(text("SELECT * FROM auditlog WHERE action = 'update' LIMIT 1"))
            row = result.fetchone()
            if row:
                print(f"Sample update log: {row}")
                # row is a tuple, changes is likely the last element or close to it
                # (id, entity_type, entity_id, action, user_id, timestamp, changes)
                print(f"Changes: {row[6]}")
            else:
                print("No update logs found")

if __name__ == "__main__":
    asyncio.run(check_data())
