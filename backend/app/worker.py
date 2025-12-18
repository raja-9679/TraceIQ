from celery import Celery
from sqlmodel import Session, create_engine
from app.core.celery_app import celery_app
from app.core.config import settings
from app.models import TestRun, TestStatus
import requests
import time

# Use sync engine for Celery worker
# Remove +asyncpg from URL for sync engine
sync_db_url = settings.DATABASE_URL.replace("+asyncpg", "")
sync_engine = create_engine(sync_db_url, echo=True)

EXECUTION_ENGINE_URL = "http://execution-engine:3000/run"

@celery_app.task(name="app.worker.run_test_suite")
def run_test_suite(run_id: int):
    with Session(sync_engine) as session:
        run = session.get(TestRun, run_id)
        if not run:
            print(f"Run {run_id} not found")
            return
        
        print(f"Starting run {run_id}")
        run.status = TestStatus.RUNNING
        session.add(run)
        session.commit()
        
        try:
            # Fetch Test Suite and Cases
            from app.models import TestSuite
            from sqlalchemy.orm import selectinload
            from sqlmodel import select
            
            suite = session.exec(
                select(TestSuite)
                .where(TestSuite.id == run.test_suite_id)
                .options(selectinload(TestSuite.test_cases))
            ).first()
            
            if not suite:
                raise Exception(f"Test Suite {run.test_suite_id} not found")
            
            # Filter cases if specific case_id is requested
            cases_to_run = suite.test_cases
            if run.test_case_id:
                cases_to_run = [c for c in suite.test_cases if c.id == run.test_case_id]
                if not cases_to_run:
                    raise Exception(f"Test Case {run.test_case_id} not found in suite {run.test_suite_id}")

            # Serialize test cases
            test_cases_data = []
            for case in cases_to_run:
                test_cases_data.append({
                    "id": case.id,
                    "name": case.name,
                    "steps": [step.dict() if hasattr(step, 'dict') else step for step in case.steps]
                })

            print(f"DEBUG: Found {len(cases_to_run)} cases to run. Serialized data: {test_cases_data}")

            payload = {
                "runId": run_id,
                "testCases": test_cases_data
            }
            
            print(f"DEBUG: Sending payload to execution engine: {payload}")

            # Call Node.js Execution Engine
            response = requests.post(EXECUTION_ENGINE_URL, json=payload)
            
            if response.status_code == 200:
                result = response.json()
                run.status = TestStatus.PASSED if result["status"] == "passed" else TestStatus.FAILED
                run.duration_ms = result.get("duration_ms", 0)
                run.trace_url = result.get("trace")
                run.video_url = result.get("video")
                run.response_status = result.get("response_status")
                run.request_headers = result.get("request_headers")
                run.response_headers = result.get("response_headers")
                if result.get("error"):
                    run.error_message = result["error"]
            else:
                run.status = TestStatus.ERROR
                run.error_message = f"Execution Engine failed: {response.text}"
                
        except Exception as e:
            print(f"Error in run {run_id}: {e}")
            run.status = TestStatus.ERROR
            run.error_message = str(e)
        
        session.add(run)
        session.commit()
        print(f"Finished run {run_id} with status {run.status}")
