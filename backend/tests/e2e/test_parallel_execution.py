import pytest
import asyncio
from httpx import AsyncClient
from sqlmodel import select
from app.models import TestSuite, TestCase, ExecutionMode, TestStatus

@pytest.mark.asyncio
async def test_parallel_execution(api_client: AsyncClient, auth_headers, setup_project):
    project_id = setup_project["project_id"]
    # 1. Create a Suite with PARALLEL execution mode
    suite_data = {
        "name": "Parallel Execution Suite",
        "description": "Tests parallel execution efficiency",
        "execution_mode": "parallel",
        "project_id": project_id
    }
    resp = await api_client.post("/suites", json=suite_data, headers=auth_headers)
    if resp.status_code != 200:
        print(f"Failed to create suite: {resp.status_code} {resp.text}")
    assert resp.status_code == 200
    suite_id = resp.json()["id"]

    # 2. Add 3 Long-Running Test Cases (using wait-timeout)
    # Each waits 2 seconds. Sequential would take 6s+. Parallel should take ~2-3s.
    case_steps = [{"id": "1", "type": "wait-timeout", "value": "2000"}]
    
    for i in range(3):
        case_data = {
            "name": f"Parallel Case {i+1}",
            "test_suite_id": suite_id,
            "project_id": project_id,
            "steps": case_steps
        }
        resp = await api_client.post(f"/suites/{suite_id}/cases", json=case_data, headers=auth_headers)
        if resp.status_code != 200:
            print(f"Failed to create case: {resp.status_code} {resp.text}")
        assert resp.status_code == 200

    # 3. Trigger Run
    resp = await api_client.post("/runs", params={"suite_id": suite_id}, headers=auth_headers)
    assert resp.status_code == 200
    run_id = resp.json()[0]["id"]

    # 4. Poll Results
    final_status = None
    for _ in range(20): # 20s max
        await asyncio.sleep(1)
        resp = await api_client.get(f"/runs/{run_id}", headers=auth_headers)
        data = resp.json()
        if data["status"] in ["passed", "failed", "error"]:
            final_status = data
            break
            
    assert final_status is not None, "Run timed out"
    print(f"Final Status: {final_status}")
    assert final_status["status"] == "passed"
    
    # 5. Verify Duration
    # Backend duration_ms should be roughly 2000ms + overhead
    duration = final_status["duration_ms"]
    print(f"Parallel Run Duration: {duration} ms")
    
    # 6 seconds would be sequential (2*3). 
    # With overhead, parallel should safely be under 5000ms.
    # Note: Worker launch + overhead might add 1-2s.
    assert duration < 5000, f"Duration {duration}ms suggests sequential execution (expected < 5000ms)"
