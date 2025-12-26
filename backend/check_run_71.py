import asyncio
from sqlmodel import select
from sqlalchemy.orm import selectinload
import os

from dotenv import load_dotenv

load_dotenv()

# But wait, I can't mock the DB URL if I don't know it.
# The previous script failed because .env is missing.
# I need to find where the DB URL is defined or how to run this.
# The user is running `npm run dev`, so the backend must be running.
# Maybe I can check the running process environment?
# Or just assume the user has the env vars set in their shell?
# But `run_command` starts a new shell.

# Let's try to find the actual database URL from the codebase or assume a default.
# In `backend/app/core/config.py`, it uses `env_file = ".env"`.
# If `.env` is missing, maybe it's in a parent dir? I checked root and it wasn't there.
# Maybe I can use `ps` to see the backend process arguments?


from app.core.database import get_session_context
from app.models import TestRun

async def check_run_71():
    async with get_session_context() as session:
        query = select(TestRun).where(TestRun.id == 71).options(selectinload(TestRun.results))
        result = await session.exec(query)
        run = result.first()
        
        if run:
            print(f"Run ID: {run.id}")
            print(f"Suite Name: {run.suite_name}")
            print(f"Test Case Name: {run.test_case_name}")
            print(f"Results Count: {len(run.results)}")
            for r in run.results:
                print(f" - {r.test_name}: {r.status}")
        else:
            print("Run 71 not found")

if __name__ == "__main__":
    asyncio.run(check_run_71())
