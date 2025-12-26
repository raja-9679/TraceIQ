import asyncio
from sqlmodel import select
from sqlalchemy.orm import selectinload
import os
from dotenv import load_dotenv

load_dotenv()

from app.core.database import get_session_context
from app.models import TestRun, TestRunRead

async def reproduce_500():
    async with get_session_context() as session:
        # Get the latest run ID
        # Specific run ID
        run_id = 71
        print(f"Testing get_run for ID: {run_id}")
        run_obj = await session.get(TestRun, run_id)

        if not run_obj:
            print("No runs found")
            return

        run_id = run_obj.id
        print(f"Testing get_run for ID: {run_id}")

        try:
            query = select(TestRun).where(TestRun.id == run_id).options(selectinload(TestRun.results))
            result = await session.exec(query)
            run = result.first()
            
            if run:
                print(f"Run found: {run.id}")
                print(f"Results: {run.results}")
                # Try validation matching endpoints.py logic
                print("Attempting manual construction like endpoints.py...")
                from app.models import TestCaseResultRead
                
                # Check model_dump
                dumped = run.model_dump()
                print("model_dump successful")
                
                # Check results validation
                results_list = [TestCaseResultRead.model_validate(r) for r in run.results]
                print(f"Results validation successful: {len(results_list)} items")
                
                response = TestRunRead(
                    **dumped,
                    results=results_list
                )
                print("Manual construction successful")
                print(response.results)
            else:
                print("Run not found")
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(reproduce_500())
