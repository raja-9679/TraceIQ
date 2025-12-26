import asyncio
from sqlalchemy import text
from app.core.database import engine
from app.models import TestRun, TestCaseResult
from sqlmodel import select
from app.core.database import get_session_context

async def check_screenshots():
    async with get_session_context() as session:
        # Check if column exists (by trying to select it)
        try:
            result = await session.exec(text("SELECT screenshots FROM testrun LIMIT 1"))
            print("testrun.screenshots column exists.")
        except Exception as e:
            print(f"testrun.screenshots column MISSING: {e}")
            return

        try:
            result = await session.exec(text("SELECT screenshots FROM testcaseresult LIMIT 1"))
            print("testcaseresult.screenshots column exists.")
        except Exception as e:
            print(f"testcaseresult.screenshots column MISSING: {e}")
            return

        # Check for data in recent runs
        print("\nChecking recent runs for screenshots...")
        query = select(TestRun).order_by(TestRun.created_at.desc()).limit(5)
        result = await session.exec(query)
        runs = result.all()
        
        for run in runs:
            print(f"Run {run.id}: screenshots={run.screenshots}")
            # Check results
            q2 = select(TestCaseResult).where(TestCaseResult.test_run_id == run.id)
            res2 = await session.exec(q2)
            results = res2.all()
            for r in results:
                print(f"  Result {r.id}: screenshots={r.screenshots}")

if __name__ == "__main__":
    import sys
    import os
    # Add backend to path
    sys.path.append(os.getcwd())
    asyncio.run(check_screenshots())
