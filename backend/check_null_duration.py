import asyncio
from sqlmodel import select
from app.core.database import get_session_context
from app.models import TestCaseResult

async def check_null_duration():
    async with get_session_context() as session:
        # Check for null duration_ms
        query = select(TestCaseResult).where(TestCaseResult.duration_ms == None)
        result = await session.exec(query)
        bad_results = result.all()
        
        if bad_results:
            print(f"Found {len(bad_results)} results with NULL duration_ms")
            for r in bad_results:
                print(f"ID: {r.id}, RunID: {r.test_run_id}")
        else:
            print("No results with NULL duration_ms found")

if __name__ == "__main__":
    asyncio.run(check_null_duration())
