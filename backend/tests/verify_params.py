import sys
import os
from unittest.mock import MagicMock, patch
import asyncio

# Add backend directory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.api import endpoints
from app.models import TestRun, TestSuite, TestCase, ExecutionMode
from app.worker import run_test_suite

async def test_params_logic():
    print(f"Testing params inheritance logic...")
    
    # Mock dependencies
    mock_session = MagicMock()
    mock_user = MagicMock()
    
    # Mock suite with settings
    mock_suite = TestSuite(
        id=1, 
        name="Test Suite", 
        execution_mode=ExecutionMode.CONTINUOUS,
        settings={"headers": {"X-Test": "1"}, "params": {"env": "staging"}}
    )
    
    # Setup session mocks
    f_suite = asyncio.Future()
    f_suite.set_result(mock_suite)
    mock_session.get.return_value = f_suite

    # Mock no sub-modules
    mock_result = MagicMock()
    mock_result.first.return_value = None
    f_exec = asyncio.Future()
    f_exec.set_result(mock_result)
    mock_session.exec.return_value = f_exec
    
    # Mock commit/refresh
    f_commit = asyncio.Future()
    f_commit.set_result(None)
    mock_session.commit.return_value = f_commit

    f_refresh = asyncio.Future()
    f_refresh.set_result(None)
    mock_session.refresh.return_value = f_refresh
    
    # Mock get_effective_settings to return the suite's settings
    # In a real scenario, this function would merge parent settings. 
    # Here we just want to verify that whatever it returns gets into the TestRun.
    effective_settings = {"headers": {"X-Test": "1"}, "params": {"env": "staging"}}
    
    with patch("app.api.endpoints.get_effective_settings", return_value=effective_settings), \
         patch("app.api.endpoints.get_suite_path", return_value="Root > Suite"), \
         patch("app.api.endpoints.run_test_suite") as mock_celery_task:
        
        # Call create_run
        run = await endpoints.create_run(suite_id=1, case_id=None, session=mock_session, current_user=mock_user)
        
        # Verify TestRun has params
        print(f"TestRun params: {run.request_params}")
        if run.request_params == {"env": "staging"}:
            print("SUCCESS: TestRun has correct request_params")
        else:
            print(f"FAILURE: TestRun params mismatch. Expected {{'env': 'staging'}}, got {run.request_params}")

        # Now verify worker logic (mocking requests.post)
        with patch("requests.post") as mock_post, \
             patch("app.worker.Session") as MockSession:
             
            # Setup worker session mock
            worker_session = MockSession.return_value.__enter__.return_value
            worker_session.get.side_effect = [run, mock_suite] # First get run, then get suite
            
            # Mock suite with cases for worker
            mock_suite_with_cases = MagicMock()
            mock_suite_with_cases.test_cases = []
            worker_session.exec.return_value.first.return_value = mock_suite_with_cases
            
            # Run worker task
            run_test_suite(run.id)
            
            # Verify payload
            if mock_post.called:
                args, kwargs = mock_post.call_args
                payload = kwargs['json']
                print(f"Worker payload globalSettings: {payload['globalSettings']}")
                if payload['globalSettings']['params'] == {"env": "staging"}:
                    print("SUCCESS: Worker payload has correct params")
                else:
                    print(f"FAILURE: Worker payload params mismatch. Expected {{'env': 'staging'}}, got {payload['globalSettings']['params']}")
            else:
                print("FAILURE: requests.post not called")

if __name__ == "__main__":
    asyncio.run(test_params_logic())
