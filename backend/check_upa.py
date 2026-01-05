import asyncio
from sqlmodel import select
from app.core.database import get_session_context
from app.models import UserProjectAccess

async def check_upa():
    async with get_session_context() as session:
        print("--- UserProjectAccess ---")
        upas = await session.exec(select(UserProjectAccess))
        for upa in upas.all():
            print(f"UPA: User {upa.user_id} -> Project {upa.project_id} | Level: {upa.access_level} | RoleID: {upa.role_id}")

if __name__ == "__main__":
    asyncio.run(check_upa())
