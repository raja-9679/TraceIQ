import asyncio
from sqlmodel import select
from app.core.database import get_session_context
from app.models import TestSuite, TestCase, TestRun, ExecutionMode, TestStatus
from app.api.endpoints import get_effective_settings, collect_test_cases, get_suite_path
from app.worker import run_test_suite

async def reproduce_create_run_internal():
    suite_id = 15
    case_id = 6
    browser = "chromium"
    device = "Desktop"
    
    async with get_session_context() as session:
        print(f"Checking suite {suite_id}...")
        suite = await session.get(TestSuite, suite_id)
        if not suite:
            print(f"Suite {suite_id} not found")
            return
        print(f"Suite execution mode: {suite.execution_mode}")

        print(f"Checking case {case_id}...")
        case = await session.get(TestCase, case_id)
        if not case:
            print(f"Case {case_id} not found")
            return
        print(f"Case steps: {case.steps}")
        if case.steps is None:
            print("WARNING: case.steps is None!")

        print("Getting effective settings...")
        try:
            effective_settings = await get_effective_settings(suite_id, session)
            print("Effective settings retrieved")
        except Exception as e:
            print(f"Error getting settings: {e}")
            import traceback
            traceback.print_exc()
            return

        print("Simulating create_run logic...")
        try:
            suite_path = await get_suite_path(suite_id, session)
            test_case_name = case.name
            
            run = TestRun(
                status=TestStatus.PENDING, 
                test_suite_id=suite_id, 
                test_case_id=case_id,
                suite_name=suite_path,
                test_case_name=test_case_name,
                request_headers=effective_settings.get("headers", {}),
                request_params=effective_settings.get("params", {}),
                allowed_domains=effective_settings.get("allowed_domains", []),
                domain_settings=effective_settings.get("domain_settings", {}),
                browser=browser,
                device=device
            )
            session.add(run)
            await session.commit()
            await session.refresh(run)
            print(f"Run created with ID: {run.id}")
            
            # Trigger Celery task (simulate)
            print("Triggering worker task...")
            # We can't easily simulate .delay() without celery worker running, 
            # but we can call the function directly if we mock the session inside it 
            # OR just assume this part fails if Redis is down.
            
            # But wait, run_test_suite is a celery task. calling it directly might not work as expected 
            # if it expects to be run in a worker context or if we want to test the queuing.
            # However, if the error is 500 from API, it might be the queuing itself.
            
            try:
                run_test_suite.delay(run.id)
                print("Task queued successfully")
            except Exception as e:
                print(f"Failed to queue task: {e}")
                # This is likely where the 500 comes from if Redis is down
                
        except Exception as e:
            print(f"Error in create_run logic: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(reproduce_create_run_internal())
