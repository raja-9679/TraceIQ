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
                # Load all cases recursively if no specific case_id (Continuous mode)
                cases_to_run = test_service.collect_cases_recursive_sync(run.test_suite_id, session)

            # Serialize test cases with their effective settings
            test_cases_data = []
            for case in cases_to_run:
                case_settings = test_service.get_effective_settings_sync(case.test_suite_id, session)
                
                test_cases_data.append({
                    "id": case.id,
                    "name": case.name,
                    "steps": [step.dict() if hasattr(step, 'dict') else step for step in case.steps],
                    "settings": case_settings,
                })

            print(f"DEBUG: Found {len(cases_to_run)} cases to run. Serialized data: {test_cases_data}")

            payload = {
                "runId": run_id,
                "testCases": test_cases_data,
                "browser": run.browser,
                "device": run.device,
                "globalSettings": {
                    "headers": run.request_headers or {},
                    "params": run.request_params or {},
                    "allowed_domains": run.allowed_domains or [],
                    "domain_settings": run.domain_settings or {}
                }
            }
            
            print(f"DEBUG: Sending payload to execution engine: {payload}")

            # Call Node.js Execution Engine
            response = requests.post(EXECUTION_ENGINE_URL, json=payload)
            
            if response.status_code == 200:
                result = response.json()
                # Update test run with results
                run.status = TestStatus.PASSED if result.get("status") == "passed" else TestStatus.FAILED
                run.duration_ms = result.get("duration_ms")
                run.error_message = result.get("error")
                run.trace_url = result.get("trace")
                run.video_url = result.get("video")
                run.screenshots = result.get("screenshots", [])
                run.response_status = result.get("response_status")
                run.request_headers = result.get("request_headers")
                run.response_headers = result.get("response_headers")
                run.network_events = result.get("network_events")
                run.execution_log = result.get("execution_log") # Save execution log
                
                # Save individual test case results
                from app.models import TestCaseResult
                
                # Clear existing results if any (for retries)
                # session.exec(delete(TestCaseResult).where(TestCaseResult.test_run_id == run_id))
                
                test_results = result.get("results", [])
                
                # Map results by ID (preferred) or Name (fallback)
                results_by_id = {}
                results_by_name = {}
                for res in test_results:
                    if res.get("test_case_id"):
                        results_by_id[res.get("test_case_id")] = res
                    results_by_name[res.get("test_name")] = res
                
                passed_count = 0
                failed_count = 0
                
                # Create results for all expected cases
                for case in cases_to_run:
                    case_res = results_by_id.get(case.id) or results_by_name.get(case.name)
                    
                    if case_res:
                        status = TestStatus.PASSED if case_res.get("status") == "passed" else TestStatus.FAILED
                        if status == TestStatus.PASSED:
                            passed_count += 1
                        else:
                            failed_count += 1
                            
                        test_result = TestCaseResult(
                            test_run_id=run.id,
                            test_name=case.name,
                            status=status,
                            duration_ms=case_res.get("duration_ms", 0),
                            error_message=case_res.get("error"),
                            trace_url=case_res.get("trace"),
                            video_url=case_res.get("video"),
                            screenshots=case_res.get("screenshots", []),
                            response_status=case_res.get("response_status"),
                            response_headers=case_res.get("response_headers"),
                            response_body=case_res.get("response_body"),
                            request_headers=case_res.get("request_headers"),
                            request_body=case_res.get("request_body"),
                            request_url=case_res.get("request_url"),
                            request_method=case_res.get("request_method"),
                            request_params=case_res.get("request_params")
                        )
                        session.add(test_result)
                    else:
                        # Case was expected but not found in results -> Skipped or Error
                        # We mark it as ERROR/FAILED so the user knows it didn't run
                        failed_count += 1
                        test_result = TestCaseResult(
                            test_run_id=run.id,
                            test_name=case.name,
                            status=TestStatus.FAILED,
                            duration_ms=0,
                            error_message="Test execution skipped or crashed before completion"
                        )
                        session.add(test_result)

                run.total_tests = len(cases_to_run)
                run.passed_tests = passed_count
                run.failed_tests = failed_count
                
                # Update main run status based on aggregated results
                if failed_count > 0:
                    run.status = TestStatus.FAILED
                else:
                    run.status = TestStatus.PASSED
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
