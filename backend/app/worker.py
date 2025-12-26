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
            # Fetch Test Suite and Cases
            from app.models import TestSuite, TestCase
            from sqlalchemy.orm import selectinload
            from sqlmodel import select
            
            suite = session.get(TestSuite, run.test_suite_id)
            if not suite:
                raise Exception(f"Test Suite {run.test_suite_id} not found")
            
            # Helper functions defined at top scope
            def collect_cases_recursive(suite_id, session):
                cases = []
                # Direct cases
                result = session.exec(select(TestSuite).where(TestSuite.id == suite_id).options(selectinload(TestSuite.test_cases)))
                s = result.first()
                if s:
                    cases.extend(s.test_cases)
                
                # Sub-modules
                result = session.exec(select(TestSuite).where(TestSuite.parent_id == suite_id))
                subs = result.all()
                for sub in subs:
                    # Check execution mode of sub-module
                    # If SEPARATE, do not recurse (it will be handled by its own run)
                    if sub.execution_mode == ExecutionMode.SEPARATE:
                        continue
                        
                    cases.extend(collect_cases_recursive(sub.id, session))
                return cases

            # Helper to calculate effective settings synchronously
            def get_effective_settings_sync(suite_id, session):
                suite = session.get(TestSuite, suite_id)
                if not suite:
                    return {"headers": {}, "params": {}, "allowed_domains": [], "domain_settings": {}}
                
                current_settings = suite.settings or {"headers": {}, "params": {}}
                
                if suite.inherit_settings and suite.parent_id:
                    parent_settings = get_effective_settings_sync(suite.parent_id, session)
                    
                    # Merge Headers & Params: Child overrides parent
                    merged_headers = {**parent_settings.get("headers", {}), **current_settings.get("headers", {})}
                    merged_params = {**parent_settings.get("params", {}), **current_settings.get("params", {})}
                    
                    # Merge Allowed Domains: Handle both strings and dicts
                    parent_domains_raw = parent_settings.get("allowed_domains", [])
                    current_domains_raw = current_settings.get("allowed_domains", [])
                    
                    # Helper to normalize to dict
                    def normalize_domain(d):
                        if not d:
                            return None
                        if isinstance(d, str):
                            return {"domain": d, "headers": True, "params": False}
                        if isinstance(d, dict) and "domain" not in d:
                            return None
                        return d

                    # Use a dict keyed by domain name to merge, favoring child (current) settings
                    merged_domains_map = {}
                    
                    for d in parent_domains_raw:
                        norm = normalize_domain(d)
                        if norm:
                            merged_domains_map[norm["domain"]] = norm
                        
                    for d in current_domains_raw:
                        norm = normalize_domain(d)
                        if norm:
                            merged_domains_map[norm["domain"]] = norm # Overwrite parent
                        
                    merged_domains = list(merged_domains_map.values())
                    
                    # Merge Domain Settings: Deep merge
                    parent_domain_settings = parent_settings.get("domain_settings", {})
                    current_domain_settings = current_settings.get("domain_settings", {})
                    merged_domain_settings = {**parent_domain_settings}
                    
                    for domain, settings in current_domain_settings.items():
                        if domain in merged_domain_settings:
                            merged_domain_settings[domain] = {
                                "headers": {**merged_domain_settings[domain].get("headers", {}), **settings.get("headers", {})},
                                "params": {**merged_domain_settings[domain].get("params", {}), **settings.get("params", {})}
                            }
                        else:
                            merged_domain_settings[domain] = settings
                            
                    return {
                        "headers": merged_headers, 
                        "params": merged_params,
                        "allowed_domains": merged_domains,
                        "domain_settings": merged_domain_settings
                    }
                
                # Ensure all keys exist
                return {
                    "headers": current_settings.get("headers", {}),
                    "params": current_settings.get("params", {}),
                    "allowed_domains": current_settings.get("allowed_domains", []),
                    "domain_settings": current_settings.get("domain_settings", {})
                }

            # Filter cases if specific case_id is requested
            if run.test_case_id:
                case = session.get(TestCase, run.test_case_id)
                if not case:
                    raise Exception(f"Test Case {run.test_case_id} not found")
                cases_to_run = [case]
            else:
                # Load all cases recursively if no specific case_id (Continuous mode)
                cases_to_run = collect_cases_recursive(run.test_suite_id, session)

            # Serialize test cases with their effective settings
            test_cases_data = []
            for case in cases_to_run:
                # Calculate effective settings for this specific case's suite
                case_settings = get_effective_settings_sync(case.test_suite_id, session)
                
                # Fetch execution mode from the suite
                # case_suite = session.get(TestSuite, case.test_suite_id)
                # execution_mode = case_suite.execution_mode.value if case_suite else "continuous"
                # Reverting: execution_mode was not present in payload before
                pass

                test_cases_data.append({
                    "id": case.id,
                    "name": case.name,
                    "steps": [step.dict() if hasattr(step, 'dict') else step for step in case.steps],
                    "settings": case_settings, # Pass effective settings for this case
                    # "executionMode": execution_mode # Reverted
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
                if not test_results and "status" in result:
                    # Single test case run or legacy format
                    # If the engine returns a single result structure, wrap it
                    pass 
                    
                # If the execution engine returns a list of results under "results" key
                if test_results:
                    for res in test_results:
                        test_result = TestCaseResult(
                            test_run_id=run.id,
                            test_name=res.get("test_name", "Unknown Test"),
                            status=TestStatus.PASSED if res.get("status") == "passed" else TestStatus.FAILED,
                            duration_ms=res.get("duration_ms", 0),
                            error_message=res.get("error"),
                        trace_url=res.get("trace"),
                        video_url=res.get("video"),
                        screenshots=res.get("screenshots", [])
                    )
                    session.add(test_result)
                else:
                    # Fallback for single case run or legacy format where "results" list is missing
                    # Create a single result record from the main run result
                    # Use run.test_case_name if available, otherwise fallback to first case name or "Single Test"
                    name = run.test_case_name
                    if not name and cases_to_run:
                        name = cases_to_run[0].name
                    if not name:
                        name = "Single Test"
                        
                    test_result = TestCaseResult(
                        test_run_id=run.id,
                        test_name=name,
                        status=run.status,
                        duration_ms=run.duration_ms or 0,
                        error_message=run.error_message,
                        trace_url=run.trace_url,
                        video_url=run.video_url,
                        screenshots=result.get("screenshots", [])
                    )
                    session.add(test_result)
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
