import asyncio
from sqlalchemy import text
from app.core.database import engine

async def add_screenshots_column():
    async with engine.begin() as conn:
        try:
            await conn.execute(text("ALTER TABLE testrun ADD COLUMN screenshots JSON"))
            print("Added screenshots column to testrun")
        except Exception as e:
            print(f"testrun screenshots column might already exist: {e}")

        try:
            await conn.execute(text("ALTER TABLE testcaseresult ADD COLUMN screenshots JSON"))
            print("Added screenshots column to testcaseresult")
        except Exception as e:
            print(f"testcaseresult screenshots column might already exist: {e}")

if __name__ == "__main__":
    asyncio.run(add_screenshots_column())
