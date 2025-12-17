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
            # Call Node.js Execution Engine
            response = requests.post(EXECUTION_ENGINE_URL, json={"runId": run_id})
            
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
