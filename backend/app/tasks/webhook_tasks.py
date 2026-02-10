"""
Celery tasks for processing webhook queue from Redis.
Uses synchronous database operations to avoid asyncpg conflicts in Celery workers.
"""
from typing import Dict, Any
import json
import redis
from sqlmodel import Session, create_engine, select
from app.core.celery_app import celery_app
from app.core.config import settings
from app.models import TestRun, TestCase, TestCaseResult, TestStatus
import time

# Use sync engine for Celery worker (same as worker.py)
sync_db_url = settings.DATABASE_URL.replace("+asyncpg", "")
sync_engine = create_engine(sync_db_url, echo=False)

# Use synchronous Redis client for Celery worker
redis_client = redis.from_url(
    settings.CELERY_BROKER_URL, decode_responses=True)

WEBHOOK_QUEUE = getattr(settings, 'REDIS_WEBHOOK_QUEUE', 'webhook:results')
WEBHOOK_FAILED_QUEUE = getattr(
    settings, 'REDIS_WEBHOOK_FAILED_QUEUE', 'webhook:failed')
MAX_RETRIES = getattr(settings, 'WEBHOOK_MAX_RETRIES', 3)
RETRY_BACKOFF = [2, 5, 10]  # seconds between retries


def process_test_run_result_sync(run_id: int, result_data: Dict[str, Any], session: Session):
    """
    Synchronous version of process_test_run_result for Celery workers.
    """
    run = session.get(TestRun, run_id)
    if not run:
        print(f"[Webhook] Run {run_id} not found")
        return

    # Check if this is a progress update or final result
    if result_data.get('type') == 'progress':
        # Progress updates - just publish to Redis, don't update DB
        return

    # Final result processing
    run.status = TestStatus.PASSED if result_data.get(
        "status") == "passed" else TestStatus.FAILED
    run.duration_ms = result_data.get("duration_ms")
    run.error_message = result_data.get("error")
    run.trace_url = result_data.get("trace")
    run.video_url = result_data.get("video")
    run.screenshots = result_data.get("screenshots", [])
    run.response_status = result_data.get("response_status")
    run.request_headers = result_data.get("request_headers")
    run.response_headers = result_data.get("response_headers")
    run.network_events = result_data.get("network_events")
    run.execution_log = result_data.get("execution_log")

    # Get test results from payload
    test_results = result_data.get("results", [])
    results_by_id = {}
    results_by_name = {}
    for res in test_results:
        if res.get("test_case_id"):
            results_by_id[res.get("test_case_id")] = res
        results_by_name[res.get("test_name")] = res

    # Determine expected cases
    if run.test_case_id:
        case = session.get(TestCase, run.test_case_id)
        cases_to_run = [case] if case else []
    else:
        # Collect cases recursively (simplified sync version)
        from app.services.test_service import test_service
        cases_to_run = test_service.collect_cases_recursive_sync(
            run.test_suite_id, session)

    # Clear existing results (idempotency)
    existing_results = session.exec(
        select(TestCaseResult).where(TestCaseResult.test_run_id == run.id))
    for res in existing_results.all():
        session.delete(res)

    passed_count = 0
    failed_count = 0

    for case in cases_to_run:
        case_res = results_by_id.get(case.id) or results_by_name.get(case.name)

        if case_res:
            status = TestStatus.PASSED if case_res.get(
                "status") == "passed" else TestStatus.FAILED
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
            # Skipped/Error
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

    if failed_count > 0:
        run.status = TestStatus.FAILED
    elif run.error_message:
        run.status = TestStatus.FAILED
    else:
        run.status = TestStatus.PASSED

    session.add(run)
    session.commit()

    print(
        f"[Webhook] Updated run {run_id}: status={run.status}, passed={passed_count}, failed={failed_count}, video={run.video_url}")


@celery_app.task(name="app.tasks.webhook_tasks.process_webhook_queue")
def process_webhook_queue():
    """
    Process webhooks from Redis queue.
    Runs periodically to consume webhook results from execution engine.
    """
    processed_count = 0

    # Process up to 10 messages per run to avoid blocking too long
    max_messages = 10

    try:
        for _ in range(max_messages):
            # RPOP to get oldest message (FIFO)
            message = redis_client.rpop(WEBHOOK_QUEUE)

            if not message:
                break  # Queue is empty

            try:
                payload = json.loads(message)
                run_id = payload.get('runId')
                result_data = payload.get('result')

                if not run_id or not result_data:
                    print(f"[Webhook] Invalid payload format: {payload}")
                    redis_client.lpush(WEBHOOK_FAILED_QUEUE, message)
                    continue

                # Process with retries using sync session
                success = False
                last_error = None

                for attempt in range(MAX_RETRIES):
                    try:
                        with Session(sync_engine) as session:
                            process_test_run_result_sync(
                                run_id, result_data, session)

                        print(f"[Webhook] Successfully processed run {run_id}")
                        success = True
                        processed_count += 1
                        break

                    except Exception as e:
                        last_error = str(e)
                        print(
                            f"[Webhook] Attempt {attempt + 1}/{MAX_RETRIES} failed for run {run_id}: {e}")
                        import traceback
                        traceback.print_exc()

                        if attempt < MAX_RETRIES - 1:
                            # Wait before retry
                            backoff = RETRY_BACKOFF[attempt] if attempt < len(
                                RETRY_BACKOFF) else RETRY_BACKOFF[-1]
                            time.sleep(backoff)

                if not success:
                    # All retries failed, move to failed queue
                    print(
                        f"[Webhook] All retries failed for run {run_id}: {last_error}")
                    failed_payload = {
                        **payload,
                        'error': last_error,
                        'failed_at': time.time()
                    }
                    redis_client.lpush(WEBHOOK_FAILED_QUEUE,
                                       json.dumps(failed_payload))

            except json.JSONDecodeError as e:
                print(f"[Webhook] Invalid JSON in queue: {e}")
                redis_client.lpush(WEBHOOK_FAILED_QUEUE, message)
            except Exception as e:
                print(f"[Webhook] Unexpected error processing message: {e}")
                redis_client.lpush(WEBHOOK_FAILED_QUEUE, message)

        if processed_count > 0:
            print(f"[Webhook] Processed {processed_count} webhooks from queue")

    except Exception as e:
        print(f"[Webhook] Error accessing Redis queue: {e}")

    return processed_count
