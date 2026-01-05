import asyncio
from sqlalchemy import text
from app.core.database import engine

async def migrate():
    print("Adding last_login_at column to users table...")
    async with engine.begin() as conn:
        try:
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMP WITHOUT TIME ZONE"))
            print("Column 'last_login_at' added successfully.")
        except Exception as e:
            print(f"Migration failed: {e}")

if __name__ == "__main__":
    asyncio.run(migrate())
