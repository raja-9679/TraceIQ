import asyncio
from sqlalchemy import text
from app.core.database import engine

async def migrate_v2():
    print("Starting database migration to v2...")
    async with engine.begin() as conn:
        try:
            # table user_settings
            print("Updating table 'user_settings'...")
            await conn.execute(text("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS multi_browser_enabled BOOLEAN DEFAULT FALSE"))
            await conn.execute(text("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS selected_browsers JSONB DEFAULT '[\"chromium\"]'"))
            await conn.execute(text("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS multi_device_enabled BOOLEAN DEFAULT FALSE"))
            await conn.execute(text("ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS selected_devices JSONB DEFAULT '[\"Desktop\"]'"))
            
            # table testsuite
            print("Updating table 'testsuite'...")
            await conn.execute(text("ALTER TABLE testsuite ADD COLUMN IF NOT EXISTS project_id INTEGER REFERENCES project(id)"))
            
            # table testcase
            print("Updating table 'testcase'...")
            await conn.execute(text("ALTER TABLE testcase ADD COLUMN IF NOT EXISTS project_id INTEGER REFERENCES project(id)"))
            
            # table testrun
            print("Updating table 'testrun'...")
            await conn.execute(text("ALTER TABLE testrun ADD COLUMN IF NOT EXISTS project_id INTEGER REFERENCES project(id)"))
            await conn.execute(text("ALTER TABLE testrun ADD COLUMN IF NOT EXISTS browser VARCHAR DEFAULT 'chromium'"))
            await conn.execute(text("ALTER TABLE testrun ADD COLUMN IF NOT EXISTS device VARCHAR"))
            await conn.execute(text("ALTER TABLE testrun ADD COLUMN IF NOT EXISTS network_events JSONB DEFAULT '[]'"))
            
            print("Migration completed successfully!")
        except Exception as e:
            print(f"Migration failed: {e}")

if __name__ == "__main__":
    asyncio.run(migrate_v2())
