import asyncio
import sys
import os
from sqlalchemy import text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import engine
from app.models import * # Import all models to ensure they are registered with SQLModel
from sqlmodel import SQLModel

async def reset_database():
    print("WARNING: Wiping all data from database and recreating schema...")
    
    # 1. Drop all tables
    async with engine.begin() as conn:
        print("Dropping all tables...")
        await conn.run_sync(SQLModel.metadata.drop_all)
        print("✅ Tables dropped.")

    # 2. Create all tables
    async with engine.begin() as conn:
        print("Creating all tables...")
        await conn.run_sync(SQLModel.metadata.create_all)
        print("✅ Tables recreated.")
            
if __name__ == "__main__":
    # Safety check
    confirm = os.environ.get("CONFIRM_RESET", "no")
    if confirm != "yes":
        print("To reset DB, run with CONFIRM_RESET=yes")
        sys.exit(1)
        
    asyncio.run(reset_database())
