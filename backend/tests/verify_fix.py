import sys
import os
from unittest.mock import MagicMock, patch
import asyncio

# Add backend directory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
from app.worker import EXECUTION_ENGINE_URL
from app.api import endpoints
from app.models import TestRun, TestSuite, TestCase, ExecutionMode

def test_config_url():
    print(f"Checking EXECUTION_ENGINE_URL...")
    expected_default = "http://execution-engine:3000/run"
    if settings.EXECUTION_ENGINE_URL == expected_default:
        print(f"SUCCESS: settings.EXECUTION_ENGINE_URL is {settings.EXECUTION_ENGINE_URL}")
    else:
        print(f"FAILURE: settings.EXECUTION_ENGINE_URL is {settings.EXECUTION_ENGINE_URL}, expected {expected_default}")

    if EXECUTION_ENGINE_URL == settings.EXECUTION_ENGINE_URL:
        print(f"SUCCESS: worker.EXECUTION_ENGINE_URL matches settings")
    else:
        print(f"FAILURE: worker.EXECUTION_ENGINE_URL is {EXECUTION_ENGINE_URL}, expected {settings.EXECUTION_ENGINE_URL}")

async def test_error_handling():
    print(f"\nTesting error handling in create_run...")
    
    # Mock dependencies
    mock_session = MagicMock()
    mock_user = MagicMock()
    
    # Mock suite and run
    mock_suite = TestSuite(id=1, name="Test Suite", execution_mode=ExecutionMode.CONTINUOUS)
    mock_run = TestRun(id=1, test_suite_id=1, status="PENDING")
    
    # Setup session mocks
    f_suite = asyncio.Future()
    f_suite.set_result(mock_suite)
    mock_session.get.return_value = f_suite

    mock_result = MagicMock()
    mock_result.first.return_value = None
    f_exec = asyncio.Future()
    f_exec.set_result(mock_result)
    mock_session.exec.return_value = f_exec

    f_commit = asyncio.Future()
    f_commit.set_result(None)
    mock_session.commit.return_value = f_commit

    f_refresh = asyncio.Future()
    f_refresh.set_result(None)
    mock_session.refresh.return_value = f_refresh
    
    # Mock get_effective_settings and get_suite_path
    with patch("app.api.endpoints.get_effective_settings", return_value={"headers": {}}), \
         patch("app.api.endpoints.get_suite_path", return_value="Root > Suite"), \
         patch("app.api.endpoints.run_test_suite") as mock_celery_task:
        
        # Simulate Celery connection error
        mock_celery_task.delay.side_effect = Exception("Redis connection failed")
        
        try:
            await endpoints.create_run(suite_id=1, case_id=None, session=mock_session, current_user=mock_user)
            print("FAILURE: create_run did not raise HTTPException")
        except Exception as e:
            from fastapi import HTTPException
            if isinstance(e, HTTPException) and e.status_code == 500 and "Failed to queue test execution" in e.detail:
                print(f"SUCCESS: Caught expected HTTPException: {e.detail}")
            else:
                print(f"FAILURE: Caught unexpected exception: {type(e)} {e}")

if __name__ == "__main__":
    test_config_url()
    asyncio.run(test_error_handling())
