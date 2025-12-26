import asyncio
from sqlalchemy import text
from app.core.database import engine

async def check_enum():
    print("Checking executionmode enum values...")
    async with engine.begin() as conn:
        try:
            result = await conn.execute(text("SELECT enumlabel FROM pg_enum JOIN pg_type ON pg_enum.enumtypid = pg_type.oid WHERE pg_type.typname = 'executionmode'"))
            labels = [row[0] for row in result]
            print(f"Enum labels: {labels}")
        except Exception as e:
            print(f"Error checking enum: {e}")

if __name__ == "__main__":
    asyncio.run(check_enum())
