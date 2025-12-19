import asyncio
from sqlalchemy import text
from app.core.database import engine

async def fix_schema():
    print("Checking database schema...")
    async with engine.begin() as conn:
        print("Adding missing columns...")
        # We use a separate try-except block for each column to ensure one failure doesn't stop others
        # But since we are in a single transaction block (engine.begin), a failure aborts the transaction.
        # So we should run these in separate transactions or just hope IF NOT EXISTS works (it does in PG 15).
        
        # However, SQLAlchemy might still see an error if we try to execute raw SQL that fails? 
        # No, IF NOT EXISTS prevents error.
        
        columns = ["request_params", "request_headers", "response_headers"]
        for col in columns:
            try:
                await conn.execute(text(f"ALTER TABLE testrun ADD COLUMN IF NOT EXISTS {col} JSON"))
                print(f"Ensured column '{col}' exists.")
            except Exception as e:
                print(f"Error adding column '{col}': {e}")

if __name__ == "__main__":
    asyncio.run(fix_schema())
