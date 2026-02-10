"""
Result Aggregator - Processes job results and updates TestRun records

This Celery task:
1. Consumes results from Redis stream
2. Creates TestCaseResult records
3. Updates TestRun progress and status
4. Publishes real-time updates via WebSocket
"""
import json
from typing import Dict, Any, Optional
import redis
from sqlmodel import Session, create_engine, select
from app.core.celery_app import celery_app
from app.core.config import settings
from app.models import TestRun, TestCaseResult, TestStatus, TestCase

# Sync database engine for Celery
sync_db_url = settings.DATABASE_URL.replace("+asyncpg", "")
sync_engine = create_engine(sync_db_url, echo=False)

# Sync Redis client
redis_client = redis.from_url(
    settings.CELERY_BROKER_URL, decode_responses=True)

RESULTS_STREAM = 'jobs:results'
CONSUMER_GROUP = 'result-processors'
CONSUMER_NAME = 'aggregator-1'


@celery_app.task(name="app.tasks.result_aggregator.process_job_results")
def process_job_results():
    """
    Process job results from Redis stream.
    Runs periodically via Celery Beat.
    """
    processed_count = 0
    max_messages = 20  # Process batch per invocation

    try:
        # Ensure consumer group exists
        try:
            redis_client.xgroup_create(
                RESULTS_STREAM, CONSUMER_GROUP, id='0', mkstream=True)
        except redis.ResponseError as e:
            if 'BUSYGROUP' not in str(e):
                raise

        # Read messages from stream
        messages = redis_client.xreadgroup(
            CONSUMER_GROUP,
            CONSUMER_NAME,
            {RESULTS_STREAM: '>'},
            count=max_messages,
            block=1000  # Block for 1 second
        )

        if not messages:
            return processed_count

        for stream_name, stream_messages in messages:
            for message_id, fields in stream_messages:
                try:
                    result_json = fields.get('result')
                    if not result_json:
                        print(
                            f"[Aggregator] Invalid message format: {message_id}")
                        redis_client.xack(
                            RESULTS_STREAM, CONSUMER_GROUP, message_id)
                        continue

                    result = json.loads(result_json)

                    # Process the result
                    process_single_result(result)

                    # Acknowledge message
                    redis_client.xack(
                        RESULTS_STREAM, CONSUMER_GROUP, message_id)
                    processed_count += 1

                except Exception as e:
                    print(
                        f"[Aggregator] Error processing message {message_id}: {e}")
                    # Don't ack - will be reprocessed

        if processed_count > 0:
            print(f"[Aggregator] Processed {processed_count} job results")

    except Exception as e:
        print(f"[Aggregator] Error accessing Redis stream: {e}")

    return processed_count


def process_single_result(result: Dict[str, Any]):
    """Process a single job result and update database"""
    run_id = result.get('run_id')
    job_id = result.get('job_id')
    test_case_id = result.get('test_case_id')
    test_name = result.get('test_name')

    if not run_id:
        print(f"[Aggregator] Missing run_id in result: {job_id}")
        return

    with Session(sync_engine) as session:
        # Get the test run
        run = session.get(TestRun, run_id)
        if not run:
            print(f"[Aggregator] Run {run_id} not found")
            return

        # Map status
        status_map = {
            'passed': TestStatus.PASSED,
            'failed': TestStatus.FAILED,
            'error': TestStatus.ERROR
        }
        status = status_map.get(result.get(
            'status', 'error'), TestStatus.ERROR)

        # Check for existing result (idempotency)
        existing = session.exec(
            select(TestCaseResult).where(
                TestCaseResult.test_run_id == run_id,
                TestCaseResult.test_name == test_name
            )
        ).first()

        artifacts = result.get('artifacts', {})
        response_data = result.get('response_data', {})
        network_events = result.get('network_events', [])

        if existing:
            # Update existing result
            existing.status = status
            existing.duration_ms = result.get('duration_ms', 0)
            existing.error_message = result.get('error')
            existing.video_url = artifacts.get('video')
            existing.trace_url = artifacts.get('trace')
            existing.screenshots = artifacts.get('screenshots', [])
            existing.response_status = response_data.get('status')
            existing.response_headers = response_data.get('headers')
            existing.response_body = response_data.get('body')
            session.add(existing)
        else:
            # Create new result
            test_result = TestCaseResult(
                test_run_id=run_id,
                test_name=test_name,
                status=status,
                duration_ms=result.get('duration_ms', 0),
                error_message=result.get('error'),
                video_url=artifacts.get('video'),
                trace_url=artifacts.get('trace'),
                screenshots=artifacts.get('screenshots', []),
                response_status=response_data.get('status'),
                response_headers=response_data.get('headers'),
                response_body=response_data.get('body')
            )
            session.add(test_result)

        # Update run with network events (accumulate from all test results)
        if network_events:
            existing_events = run.network_events or []
            # Add test name to each event for identification
            for event in network_events:
                event['testCaseName'] = test_name
            run.network_events = existing_events + network_events

        # Update run progress from Redis
        progress = redis_client.hgetall(f'runs:{run_id}:progress')

        if progress:
            run.total_tests = int(progress.get('total', run.total_tests))
            run.passed_tests = int(progress.get('passed', 0))
            run.failed_tests = int(progress.get('failed', 0))

            # Check if run is complete
            completed = int(progress.get('completed', 0))
            total = int(progress.get('total', 0))

            if completed >= total and total > 0:
                # All jobs complete - finalize run
                if run.failed_tests > 0:
                    run.status = TestStatus.FAILED
                else:
                    run.status = TestStatus.PASSED

                # Calculate total duration
                results = session.exec(
                    select(TestCaseResult).where(
                        TestCaseResult.test_run_id == run_id)
                ).all()
                run.duration_ms = sum(r.duration_ms or 0 for r in results)

                # For single-test runs, copy video/trace from result to run level
                # This ensures consistent UI display for both CONTINUOUS and SEPARATE modes
                if len(results) == 1 and results[0]:
                    if results[0].video_url and not run.video_url:
                        run.video_url = results[0].video_url
                    if results[0].trace_url and not run.trace_url:
                        run.trace_url = results[0].trace_url
                elif len(results) > 1:
                    # For multi-test runs, use the first result's artifacts as run-level
                    # (or you could combine them, but single video makes more sense for UI)
                    first_with_video = next(
                        (r for r in results if r.video_url), None)
                    first_with_trace = next(
                        (r for r in results if r.trace_url), None)
                    if first_with_video and not run.video_url:
                        run.video_url = first_with_video.video_url
                    if first_with_trace and not run.trace_url:
                        run.trace_url = first_with_trace.trace_url

                print(
                    f"[Aggregator] Run {run_id} completed: {run.passed_tests} passed, {run.failed_tests} failed")
            else:
                run.status = TestStatus.RUNNING

        session.add(run)
        session.commit()

        # Publish real-time update
        publish_progress_update(run_id, {
            'run_id': run_id,
            'type': 'progress' if run.status == TestStatus.RUNNING else 'complete',
            'status': run.status.value,
            'passed_tests': run.passed_tests,
            'failed_tests': run.failed_tests,
            'total_tests': run.total_tests,
            'latest_test': test_name,
            'latest_status': result.get('status')
        })


def publish_progress_update(run_id: int, payload: Dict[str, Any]):
    """Publish progress update via Redis pub/sub"""
    try:
        redis_client.publish(f'run:{run_id}', json.dumps(payload))
    except Exception as e:
        print(f"[Aggregator] Failed to publish update for run {run_id}: {e}")


@celery_app.task(name="app.tasks.result_aggregator.check_stale_runs")
def check_stale_runs():
    """
    Check for runs that have stale progress (jobs not completing).
    Runs periodically to detect and handle stuck runs.
    """
    from datetime import datetime, timedelta

    with Session(sync_engine) as session:
        # Find runs that are RUNNING but haven't had updates in 10 minutes
        cutoff = datetime.utcnow() - timedelta(minutes=10)

        stale_runs = session.exec(
            select(TestRun).where(
                TestRun.status == TestStatus.RUNNING,
                TestRun.created_at < cutoff
            )
        ).all()

        for run in stale_runs:
            # Check Redis progress
            progress = redis_client.hgetall(f'runs:{run.id}:progress')

            if not progress:
                # No progress tracking - likely old run
                run.status = TestStatus.ERROR
                run.error_message = "Test execution timed out - no progress data"
                session.add(run)
                print(
                    f"[Aggregator] Marked run {run.id} as ERROR (no progress)")
                continue

            completed = int(progress.get('completed', 0))
            total = int(progress.get('total', 0))

            if completed < total:
                # Some jobs haven't completed
                run.status = TestStatus.ERROR
                run.error_message = f"Test execution timed out - {completed}/{total} jobs completed"
                run.total_tests = total
                run.passed_tests = int(progress.get('passed', 0))
                run.failed_tests = int(progress.get(
                    'failed', 0)) + (total - completed)
                session.add(run)
                print(
                    f"[Aggregator] Marked run {run.id} as ERROR (timeout: {completed}/{total})")

        session.commit()


# Register tasks with Celery Beat
celery_app.conf.beat_schedule.update({
    'process-job-results': {
        'task': 'app.tasks.result_aggregator.process_job_results',
        'schedule': 2.0,  # Every 2 seconds
    },
    'check-stale-runs': {
        'task': 'app.tasks.result_aggregator.check_stale_runs',
        'schedule': 60.0,  # Every minute
    },
})
