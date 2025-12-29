import asyncio
import os
from dotenv import load_dotenv

# Load .env from backend directory
load_dotenv("backend/.env")

from app.core.database import engine
from sqlalchemy import text

async def migrate():
    async with engine.begin() as conn:
        # Add columns to testsuite
        await conn.execute(text("ALTER TABLE testsuite ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc')"))
        await conn.execute(text("ALTER TABLE testsuite ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc')"))
        await conn.execute(text("ALTER TABLE testsuite ADD COLUMN IF NOT EXISTS created_by_id INTEGER REFERENCES users(id)"))
        await conn.execute(text("ALTER TABLE testsuite ADD COLUMN IF NOT EXISTS updated_by_id INTEGER REFERENCES users(id)"))
        print("Columns added to 'testsuite' table.")

        # Add columns to testcase
        await conn.execute(text("ALTER TABLE testcase ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc')"))
        await conn.execute(text("ALTER TABLE testcase ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc')"))
        await conn.execute(text("ALTER TABLE testcase ADD COLUMN IF NOT EXISTS created_by_id INTEGER REFERENCES users(id)"))
        await conn.execute(text("ALTER TABLE testcase ADD COLUMN IF NOT EXISTS updated_by_id INTEGER REFERENCES users(id)"))
        print("Columns added to 'testcase' table.")

        # Create auditlog table
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS auditlog (
                id SERIAL PRIMARY KEY,
                entity_type VARCHAR NOT NULL,
                entity_id INTEGER NOT NULL,
                action VARCHAR NOT NULL,
                user_id INTEGER REFERENCES users(id),
                timestamp TIMESTAMP WITHOUT TIME ZONE DEFAULT (now() at time zone 'utc'),
                changes JSONB DEFAULT '{}'::jsonb
            )
        """))
        print("Table 'auditlog' created successfully.")

if __name__ == "__main__":
    asyncio.run(migrate())
