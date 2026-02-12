import pytest
import asyncio
import httpx
import json

# Mark all tests as async
pytestmark = pytest.mark.asyncio

async def create_suite_and_case(api_client, auth_headers, project_id, steps):
    # Create Suite
    suite_resp = await api_client.post( "/suites", json={"name": "E2E Suite", "project_id": project_id, "execution_mode": "continuous"}, headers=auth_headers)
    assert suite_resp.status_code in [200, 201]
    suite_id = suite_resp.json()["id"]

    # Create Case
    case_payload = {
        "name": "E2E Test Case",
        "steps": steps
    }
    case_resp = await api_client.post(f"/suites/{suite_id}/cases", json=case_payload, headers=auth_headers)
    assert case_resp.status_code in [200, 201]
    
    return suite_id

async def poll_run(api_client, auth_headers, run_id, timeout=30):
    for _ in range(timeout):
        resp = await api_client.get(f"/runs/{run_id}", headers=auth_headers)
        status = resp.json()["status"]
        if status in ["passed", "failed", "error"]:
            return resp.json()
        await asyncio.sleep(1)
    return None

async def test_successful_run(api_client, auth_headers, setup_project):
    project_id = setup_project["project_id"]
    steps = [
        {"id": "1", "type": "goto", "value": "about:blank"}
    ]
    suite_id = await create_suite_and_case(api_client, auth_headers, project_id, steps)
    
    # Trigger Run
    run_resp = await api_client.post(f"/runs?suite_id={suite_id}", headers=auth_headers)
    assert run_resp.status_code in [200, 202] # 202 Accepted
    run_data = run_resp.json()
    # It might return list or single object depending on API implementation (triggerRun returns list usually if suite, or list of runs)
    # The current API returns list of runs for suite trigger
    assert isinstance(run_data, list)
    run_id = run_data[0]["id"]
    
    result = await poll_run(api_client, auth_headers, run_id)
    assert result is not None, "Run timed out"
    assert result["status"] == "passed"
    assert result["passed_tests"] == 1

async def test_failed_run(api_client, auth_headers, setup_project):
    project_id = setup_project["project_id"]
    # Intentionally fail by waiting for non-existent selector
    steps = [
        {"id": "1", "type": "goto", "value": "about:blank"},
        {"id": "2", "type": "click", "selector": "#non-existent-id-12345", "timeout": 2000} # Short timeout to fail fast
    ]
    suite_id = await create_suite_and_case(api_client, auth_headers, project_id, steps)
    
    run_resp = await api_client.post(f"/runs?suite_id={suite_id}", headers=auth_headers)
    run_id = run_resp.json()[0]["id"]
    
    result = await poll_run(api_client, auth_headers, run_id)
    assert result is not None, "Run timed out"
    assert result["status"] == "failed"
    assert result["failed_tests"] == 1
    # Check if error message is captured
    # Detailed results are in 'results' array usually
    assert len(result.get("results", [])) > 0
    assert result["results"][0]["status"] == "failed"

async def test_concurrent_runs(api_client, auth_headers, setup_project):
    project_id = setup_project["project_id"]
    steps = [{"id": "1", "type": "goto", "value": "about:blank"}]
    suite_id = await create_suite_and_case(api_client, auth_headers, project_id, steps)
    
    # Trigger 5 runs concurrently
    tasks = []
    for _ in range(5):
        tasks.append(api_client.post(f"/runs?suite_id={suite_id}", headers=auth_headers))
    
    responses = await asyncio.gather(*tasks)
    run_ids = [r.json()[0]["id"] for r in responses]
    
    # Poll all
    poll_tasks = [poll_run(api_client, auth_headers, rid) for rid in run_ids]
    results = await asyncio.gather(*poll_tasks)
    
    for res in results:
        assert res is not None
        assert res["status"] == "passed"
