"""
Result Aggregator - Processes job results and updates TestRun records

This Celery task:
1. Consumes results from Redis stream
2. Creates TestCaseResult records
3. Updates TestRun progress and status
4. Publishes real-time updates via WebSocket

Staleness Detection Strategy
----------------------------
A run is considered "stuck" ONLY if no new job result has arrived within
STALE_RUN_INACTIVITY_MINUTES (default 15 min). Large suites (700+ tests)
may legitimately run for hours if the worker pool is small — they must NOT
be killed purely based on wall-clock age.

Every time a result is processed, we record `last_progress_at` in Redis:
    HSET runs:{id}:progress last_progress_at <iso-timestamp>

check_stale_runs() reads this field to decide whether a run is inactive.
"""
import json
from datetime import datetime
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

    # Persist a captured auth session (storageState) if the job ran a
    # passing auth-setup case. Job-level field in both single and
    # continuous results.
    _persist_auth_state(result)

    # Check if this is a multi-test continuous job result
    test_results = result.get('test_results')

    if test_results and len(test_results) > 0:
        # Multi-test continuous job - process each test result
        process_continuous_job_result(run_id, job_id, result, test_results)
    else:
        # Single test job - original behavior
        process_single_test_result(run_id, job_id, result)


def _persist_auth_state(result: Dict[str, Any]):
    """Upsert the project's AuthSession from a worker-captured storageState."""
    auth_state = result.get('auth_state')
    if not auth_state:
        return

    case_id = result.get('auth_case_id') or result.get('test_case_id')
    if not case_id:
        return

    from datetime import datetime
    from app.models import TestCase, AuthSession

    try:
        with Session(sync_engine) as session:
            case = session.get(TestCase, case_id)
            if not case or not case.project_id:
                print(f"[Aggregator] auth_state received but case {case_id} "
                      f"has no project — skipped")
                return
            auth = session.exec(
                select(AuthSession).where(
                    AuthSession.project_id == case.project_id)
            ).first()
            if auth:
                auth.storage_state = auth_state
                auth.captured_by_case_id = case.id
                auth.captured_at = datetime.utcnow()
            else:
                auth = AuthSession(
                    project_id=case.project_id,
                    storage_state=auth_state,
                    captured_by_case_id=case.id,
                )
            session.add(auth)
            session.commit()
            print(f"[Aggregator] Stored auth session for project "
                  f"{case.project_id} (captured by case {case.id})")
    except Exception as e:
        print(f"[Aggregator] Failed to persist auth session: {e}")


def update_run_from_progress(run: TestRun, run_id: int, progress: Dict[str, str], session: Session):
    """Update run status and progress from Redis progress tracking"""
    if not progress:
        return

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
            # Clear stale timeout error_message; the real failures are in
            # individual TestCaseResult records.
            run.error_message = None
        else:
            run.status = TestStatus.PASSED
            # Clear any stale error_message (e.g. from a previous timeout
            # marking by check_stale_runs that was later resolved by
            # arriving results).
            run.error_message = None

        # Calculate wall-clock duration from when the first worker actually
        # picked up a job (worker_started_at), not from queue dispatch time
        # (created_at). Falls back to created_at if the field is absent.
        from datetime import datetime, timezone
        worker_started_str = progress.get('worker_started_at') if progress else None
        start_ref = None
        if worker_started_str:
            try:
                start_ref = datetime.fromisoformat(worker_started_str)
            except ValueError:
                start_ref = None
        if start_ref is None and run.created_at:
            start_ref = run.created_at
        if start_ref is not None:
            # Handle timezone-aware vs naive datetime
            now = datetime.now(
                timezone.utc) if getattr(start_ref, 'tzinfo', None) else datetime.utcnow()
            run.duration_ms = int(
                (now - start_ref).total_seconds() * 1000)

        # Get results for artifact copying
        results = session.exec(
            select(TestCaseResult).where(
                TestCaseResult.test_run_id == run_id)
        ).all()

        # Copy video/trace from results to run level
        if len(results) == 1 and results[0]:
            if results[0].video_url and not run.video_url:
                run.video_url = results[0].video_url
            if results[0].trace_url and not run.trace_url:
                run.trace_url = results[0].trace_url
        elif len(results) > 1:
            # For multi-test runs, use the first result's artifacts as run-level
            first_with_video = next(
                (r for r in results if r.video_url), None)
            first_with_trace = next(
                (r for r in results if r.trace_url), None)
            if first_with_video and not run.video_url:
                run.video_url = first_with_video.video_url
            if first_with_trace and not run.trace_url:
                run.trace_url = first_with_trace.trace_url

        print(
            f"[Aggregator] Run {run_id} completed: {run.passed_tests} passed, {run.failed_tests} failed, duration={run.duration_ms}ms")
    else:
        run.status = TestStatus.RUNNING


def process_continuous_job_result(run_id: int, job_id: str, result: Dict[str, Any], test_results: list):
    """Process a continuous job result with multiple test cases"""
    if not run_id:
        print(f"[Aggregator] Missing run_id in continuous result: {job_id}")
        return

    with Session(sync_engine) as session:
        # Get the test run
        run = session.get(TestRun, run_id)
        if not run:
            print(f"[Aggregator] Run {run_id} not found")
            return

        status_map = {
            'passed': TestStatus.PASSED,
            'failed': TestStatus.FAILED,
            'error': TestStatus.ERROR
        }

        # Job-level artifacts (shared video/trace for all tests in this job)
        artifacts = result.get('artifacts', {})
        job_video = artifacts.get('video')
        job_trace = artifacts.get('trace')
        network_events = result.get('network_events', [])

        # Process each test result in the job
        for test_res in test_results:
            test_name = test_res.get('test_name')
            status = status_map.get(test_res.get(
                'status', 'error'), TestStatus.ERROR)
            response_data = test_res.get('response_data', {})
            request_data = response_data.get(
                'request', {}) if response_data else {}

            # Check for existing result (idempotency)
            existing = session.exec(
                select(TestCaseResult).where(
                    TestCaseResult.test_run_id == run_id,
                    TestCaseResult.test_name == test_name
                )
            ).first()

            if existing:
                # Update existing result
                existing.status = status
                existing.duration_ms = test_res.get('duration_ms', 0)
                existing.error_message = test_res.get('error')
                # Use job-level video/trace for all tests (shared browser)
                existing.video_url = job_video
                existing.trace_url = job_trace
                existing.response_status = response_data.get('status')
                existing.response_headers = response_data.get('headers')
                existing.response_body = response_data.get('body')
                existing.request_headers = request_data.get('headers')
                existing.request_body = request_data.get('body')
                existing.request_url = request_data.get('url')
                existing.request_method = request_data.get('method')
                existing.request_params = request_data.get('params')
                session.add(existing)
            else:
                # Create new result
                test_result = TestCaseResult(
                    test_run_id=run_id,
                    test_name=test_name,
                    status=status,
                    duration_ms=test_res.get('duration_ms', 0),
                    error_message=test_res.get('error'),
                    video_url=job_video,
                    trace_url=job_trace,
                    screenshots=artifacts.get('screenshots', []),
                    response_status=response_data.get('status'),
                    response_headers=response_data.get('headers'),
                    response_body=response_data.get('body'),
                    request_headers=request_data.get('headers'),
                    request_body=request_data.get('body'),
                    request_url=request_data.get('url'),
                    request_method=request_data.get('method'),
                    request_params=request_data.get('params')
                )
                session.add(test_result)

        # Update run with network events (tagged by test name already)
        if network_events:
            existing_events = run.network_events or []
            run.network_events = existing_events + network_events

        # Record last-activity timestamp so check_stale_runs() knows this run
        # is still progressing (prevents false timeout on large suites)
        redis_client.hset(
            f'runs:{run_id}:progress',
            'last_progress_at',
            datetime.utcnow().isoformat()
        )

        # Update run progress from Redis
        progress = redis_client.hgetall(f'runs:{run_id}:progress')
        update_run_from_progress(run, run_id, progress, session)

        session.add(run)
        session.commit()

        # Publish real-time update for each test
        for test_res in test_results:
            publish_progress_update(run_id, {
                'run_id': run_id,
                'type': 'progress' if run.status == TestStatus.RUNNING else 'complete',
                'status': run.status.value,
                'passed_tests': run.passed_tests,
                'failed_tests': run.failed_tests,
                'total_tests': run.total_tests,
                'latest_test': test_res.get('test_name'),
                'latest_status': test_res.get('status')
            })


def process_single_test_result(run_id: int, job_id: str, result: Dict[str, Any]):
    """Process a single test job result (original behavior)"""
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
        request_data = response_data.get(
            'request', {}) if response_data else {}
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
            existing.request_headers = request_data.get('headers')
            existing.request_body = request_data.get('body')
            existing.request_url = request_data.get('url')
            existing.request_method = request_data.get('method')
            existing.request_params = request_data.get('params')
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
                response_body=response_data.get('body'),
                request_headers=request_data.get('headers'),
                request_body=request_data.get('body'),
                request_url=request_data.get('url'),
                request_method=request_data.get('method'),
                request_params=request_data.get('params')
            )
            session.add(test_result)

        # Update run with network events (accumulate from all test results)
        if network_events:
            existing_events = run.network_events or []
            # Add test name to each event for identification
            for event in network_events:
                event['testCaseName'] = test_name
            run.network_events = existing_events + network_events

        # Record last-activity timestamp so check_stale_runs() knows this run
        # is still progressing (prevents false timeout on large suites)
        redis_client.hset(
            f'runs:{run_id}:progress',
            'last_progress_at',
            datetime.utcnow().isoformat()
        )

        # Update run progress from Redis
        progress = redis_client.hgetall(f'runs:{run_id}:progress')
        update_run_from_progress(run, run_id, progress, session)

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
    Check for runs that have stale progress (no new results arriving).

    A run is only considered "stuck" if:
      1. No job result has arrived within STALE_RUN_INACTIVITY_MINUTES, OR
      2. The run has been RUNNING longer than MAX_RUN_DURATION_HOURS (hard cap).

    IMPORTANT: A run with 700+ test cases legitimately takes hours. We must NOT
    kill it just because it is old — only kill it if progress has STOPPED.
    """
    from datetime import timedelta

    inactivity_minutes = getattr(settings, 'STALE_RUN_INACTIVITY_MINUTES', 15)
    max_hours = getattr(settings, 'MAX_RUN_DURATION_HOURS', 6)

    inactivity_cutoff = datetime.utcnow() - timedelta(minutes=inactivity_minutes)
    absolute_cutoff = datetime.utcnow() - timedelta(hours=max_hours)

    with Session(sync_engine) as session:
        # Load all currently RUNNING runs — no created_at filter so we can
        # apply nuanced logic per-run based on their Redis progress data.
        running_runs = session.exec(
            select(TestRun).where(TestRun.status == TestStatus.RUNNING)
        ).all()

        killed = 0
        for run in running_runs:
            progress = redis_client.hgetall(f'runs:{run.id}:progress')

            # Case 1: Absolute hard-cap exceeded — kill regardless of activity
            if run.created_at and run.created_at < absolute_cutoff:
                _mark_run_timeout(
                    run, progress, session,
                    reason=f"exceeded maximum run duration ({max_hours}h hard cap)"
                )
                killed += 1
                continue

            # Case 2: No Redis progress data at all
            if not progress:
                # Only kill if the run was created more than inactivity_minutes ago
                # (gives the dispatcher time to write the progress hash after creation)
                if run.created_at and run.created_at < inactivity_cutoff:
                    _mark_run_timeout(
                        run, progress, session,
                        reason="no progress data found in Redis"
                    )
                    killed += 1
                continue

            # Case 3: Has progress data — check last_progress_at timestamp
            last_progress_str = progress.get('last_progress_at')
            completed = int(progress.get('completed', 0))
            total = int(progress.get('total', 0))

            # If all jobs completed but run is still RUNNING, the aggregator
            # update_run_from_progress() will handle finalizing it on the next
            # process_job_results() cycle. Don't interfere here.
            if total > 0 and completed >= total:
                continue

            if last_progress_str:
                try:
                    last_progress_at = datetime.fromisoformat(last_progress_str)
                except ValueError:
                    last_progress_at = None

                if last_progress_at and last_progress_at < inactivity_cutoff:
                    # Progress has stalled — real timeout
                    _mark_run_timeout(
                        run, progress, session,
                        reason=f"no job results received for {inactivity_minutes}+ minutes"
                    )
                    killed += 1
                # else: last progress was recent — run is healthy, skip
            else:
                # last_progress_at not yet recorded (first result not arrived yet).
                # If a worker has already picked up a job (worker_started_at is set),
                # use that as the activity baseline so we don't false-timeout a run
                # that is legitimately waiting in a busy queue.
                # If no worker has touched the run yet, fall back to created_at.
                worker_started_str = progress.get('worker_started_at')
                baseline = None
                if worker_started_str:
                    try:
                        baseline = datetime.fromisoformat(worker_started_str)
                    except ValueError:
                        pass
                if baseline is None:
                    baseline = run.created_at
                if baseline and baseline < inactivity_cutoff:
                    _mark_run_timeout(
                        run, progress, session,
                        reason="no initial results arrived within expected time"
                    )
                    killed += 1

        if killed > 0:
            session.commit()
            print(f"[Aggregator] Marked {killed} stale run(s) as ERROR")
        else:
            print(f"[Aggregator] check_stale_runs: {len(running_runs)} running, all healthy")


def _mark_run_timeout(
    run: TestRun,
    progress: Optional[Dict[str, str]],
    session: Session,
    reason: str
):
    """
    Mark a run as ERROR due to timeout/staleness.
    Includes partial progress counts from Redis if available.
    """
    completed = int(progress.get('completed', 0)) if progress else 0
    total = int(progress.get('total', run.total_tests or 0)) if progress else (run.total_tests or 0)
    passed = int(progress.get('passed', 0)) if progress else run.passed_tests
    failed = int(progress.get('failed', 0)) if progress else run.failed_tests

    run.status = TestStatus.ERROR
    run.error_message = (
        f"Test execution timed out ({reason}). "
        f"{completed}/{total} jobs completed before timeout."
    )
    if total > 0:
        run.total_tests = total
        run.passed_tests = passed
        # Count incomplete jobs as failures
        run.failed_tests = failed + max(0, total - completed)

    session.add(run)
    print(f"[Aggregator] Run {run.id} → ERROR: {reason} ({completed}/{total} completed)")


# NOTE: Beat schedule is registered in app/core/celery_app.py.
# Do NOT add beat_schedule.update() here — it would create duplicate entries
# and the last registration silently overrides the first.
