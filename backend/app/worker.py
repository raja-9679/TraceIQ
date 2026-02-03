from celery import Celery
from sqlmodel import Session, create_engine
from app.core.celery_app import celery_app
from app.core.config import settings
from app.models import TestRun, TestStatus, ExecutionMode
import requests
import time

# Use sync engine for Celery worker
# Remove +asyncpg from URL for sync engine
sync_db_url = settings.DATABASE_URL.replace("+asyncpg", "")
sync_engine = create_engine(sync_db_url, echo=True)

EXECUTION_ENGINE_URL = settings.EXECUTION_ENGINE_URL

@celery_app.task(name="app.worker.run_test_suite")
def run_test_suite(run_id: int):
    with Session(sync_engine) as session:
        run = session.get(TestRun, run_id)
        if not run:
            print(f"Run {run_id} not found")
            return
        
        print(f"Starting run {run_id}")
        print(f"DEBUG: Run attributes: {run}")
        try:
             print(f"DEBUG: run.browser = {run.browser}")
        except Exception as e:
             print(f"DEBUG: Could not access run.browser: {e}")
        
        run.status = TestStatus.RUNNING
        session.add(run)
        session.commit()
        
        try:
            from app.models import TestSuite, TestCase
            from app.services.test_service import test_service
            
            # Filter cases if specific case_id is requested
            if run.test_case_id:
                case = session.get(TestCase, run.test_case_id)
                if not case:
                    raise Exception(f"Test Case {run.test_case_id} not found")
                cases_to_run = [case]
            else:
                # Load all cases recursively (Sync)
                cases_to_run = test_service.collect_cases_recursive_sync(run.test_suite_id, session)

            # Serialize test cases
            test_cases_data = []
            for case in cases_to_run:
                case_settings = test_service.get_effective_settings_sync(case.test_suite_id, session)
                
                test_cases_data.append({
                    "id": case.id,
                    "name": case.name,
                    "steps": [step.dict() if hasattr(step, 'dict') else step for step in case.steps],
                    "settings": case_settings,
                })

            # Fetch Suite Execution Mode
            suite = session.get(TestSuite, run.test_suite_id)
            execution_mode = suite.execution_mode.value if suite else "continuous"

            print(f"DEBUG: Found {len(cases_to_run)} cases. Execution Mode: {execution_mode}. Sending async request.")

            # Construct Callback URL (Internal Docker Network)
            # Assuming backend is reachable at 'backend' hostname in docker-compose network
            callback_url = f"http://backend:8000/api/runs/{run_id}/webhook"

            payload = {
                "runId": run_id,
                "testCases": test_cases_data,
                "browser": run.browser,
                "device": run.device,
                "executionMode": execution_mode,
                "globalSettings": {
                    "headers": run.request_headers or {},
                    "params": run.request_params or {},
                    "allowed_domains": run.allowed_domains or [],
                    "domain_settings": run.domain_settings or {}
                },
                "callbackUrl": callback_url,
                "webhookSecret": settings.SECRET_KEY
            }
            
            # Call Node.js Execution Engine (Fire and Forget)
            # Short timeout because we expect immediate 202
            response = requests.post(EXECUTION_ENGINE_URL, json=payload, timeout=10)
            
            if response.status_code in [200, 202]:
                print(f"Execution request accepted for run {run_id}")
            else:
                raise Exception(f"Execution Engine rejected request: {response.status_code} {response.text}")
                
        except Exception as e:
            print(f"Error in run {run_id}: {e}")
            run.status = TestStatus.ERROR
            run.error_message = str(e)
        
        session.add(run)
        session.commit()
        print(f"Finished run {run_id} with status {run.status}")
