import asyncio
import sys
import os
from sqlalchemy import text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import get_session_context

async def migrate_invitation_schema():
    print("Migrating OrganizationInvitation schema...")
    async with get_session_context() as session:
        # Add token column
        try:
            await session.exec(text("ALTER TABLE organizationinvitation ADD COLUMN token VARCHAR(255)"))
            await session.exec(text("CREATE UNIQUE INDEX ix_organizationinvitation_token ON organizationinvitation (token)"))
            print("Added 'token' column.")
        except Exception as e:
            print(f"Token column might already exist: {e}")

        # Add expires_at column
        try:
            await session.exec(text("ALTER TABLE organizationinvitation ADD COLUMN expires_at TIMESTAMP WITHOUT TIME ZONE"))
            print("Added 'expires_at' column.")
        except Exception as e:
            print(f"Expires_at column might already exist: {e}")
        
        await session.commit()
    print("Migration complete.")

if __name__ == "__main__":
    asyncio.run(migrate_invitation_schema())
