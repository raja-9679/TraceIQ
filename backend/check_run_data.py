import asyncio
from sqlmodel import select, desc
import os
from dotenv import load_dotenv

load_dotenv()

from app.core.database import get_session_context
from app.models import TestRun

async def check_latest_run():
    async with get_session_context() as session:
        result = await session.exec(select(TestRun).order_by(desc(TestRun.id)).limit(1))
        run = result.first()
        if run:
            print(f"Run ID: {run.id}")
            print(f"Suite Name: {run.suite_name}")
            print(f"Test Case Name: {run.test_case_name}")
            print(f"Test Case ID: {run.test_case_id}")
        else:
            print("No runs found")

if __name__ == "__main__":
    asyncio.run(check_latest_run())
