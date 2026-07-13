"""
Test Execution Worker - Dispatches test runs to execution workers

Architecture:
- CONTINUOUS mode: Single-engine execution with shared browser context
- SEPARATE mode: Distributed workers, one test per worker (complete isolation)
"""
from celery import Celery
from sqlmodel import Session, create_engine
from app.core.celery_app import celery_app
from app.core.config import settings
from app.models import TestRun, TestStatus, ExecutionMode
import requests
import json
import redis

# Use sync engine for Celery worker
sync_db_url = settings.DATABASE_URL.replace("+asyncpg", "")
sync_engine = create_engine(sync_db_url, echo=False)

# Redis client for job dispatching
redis_client = redis.from_url(
    settings.CELERY_BROKER_URL, decode_responses=True)

# Legacy execution engine URL (for CONTINUOUS mode)
EXECUTION_ENGINE_URL = settings.EXECUTION_ENGINE_URL

# Feature flag for new architecture
USE_DISTRIBUTED_EXECUTION = getattr(
    settings, 'USE_DISTRIBUTED_EXECUTION', True)

# Webhook secret for internal service-to-service calls
_WEBHOOK_SECRET = getattr(settings, 'WEBHOOK_SECRET', None) or settings.SECRET_KEY


@celery_app.task(name="app.worker.run_test_suite")
def run_test_suite(run_id: int):
    """
    Main entry point for test execution.
    Routes to appropriate execution strategy based on execution mode.
    """
    with Session(sync_engine) as session:
        run = session.get(TestRun, run_id)
        if not run:
            print(f"[Worker] Run {run_id} not found")
            return

        print(f"[Worker] Starting run {run_id}")

        run.status = TestStatus.RUNNING
        session.add(run)
        session.commit()

        try:
            from app.models import TestSuite, TestCase
            from app.services.test_service import test_service

            # Load test cases
            if run.test_case_id:
                case = session.get(TestCase, run.test_case_id)
                if not case:
                    raise Exception(f"Test Case {run.test_case_id} not found")
                cases_to_run = [case]
            else:
                cases_to_run = test_service.collect_cases_recursive_sync(
                    run.test_suite_id, session)

            if not cases_to_run:
                raise Exception("No test cases found to execute")

            # Phase B: drop quarantined test cases before dispatch so flaky
            # tests don't gate AI-agent regressions. The flake records live
            # at the test_case level; sub-step granularity is informational.
            cases_to_run = _filter_quarantined(cases_to_run, session)

            # Get execution mode
            suite = session.get(TestSuite, run.test_suite_id)
            execution_mode = suite.execution_mode if suite else ExecutionMode.CONTINUOUS

            # Get effective settings
            effective_settings = test_service.get_effective_settings_sync(
                run.test_suite_id, session)

            # Inject the project's stored auth session (Playwright
            # storageState) so cases start already logged in. Workers skip
            # it for auth-setup cases and cases with use_auth_session=False.
            auth_project_id = run.project_id or (
                cases_to_run[0].project_id if cases_to_run else None)
            auth_state = _load_auth_state(auth_project_id, session)
            if auth_state:
                effective_settings['storage_state'] = auth_state
                print(f"[Worker] Run {run_id}: injecting stored auth session "
                      f"for project {auth_project_id}")

            # Update total tests count
            run.total_tests = len(cases_to_run)
            session.add(run)
            session.commit()

            print(
                f"[Worker] Run {run_id}: {len(cases_to_run)} cases, mode={execution_mode.value}")

            # ALWAYS use distributed execution - dispatch ALL test cases to Redis queue
            # Workers will pick one job at a time and process them
            if USE_DISTRIBUTED_EXECUTION:
                # If a specific test case was requested, dispatch only that case
                if run.test_case_id:
                    # Single test case execution
                    dispatch_cases_to_queue(
                        run, cases_to_run, effective_settings, session)
                elif execution_mode == ExecutionMode.SEPARATE:
                    # SEPARATE mode: Check for sub-structure (sub-suites run as groups)
                    execution_units = test_service.collect_execution_units_sync(
                        run.test_suite_id, session)

                    if execution_units:
                        dispatch_separate_jobs(
                            run, execution_units, effective_settings, session)
                    else:
                        # No sub-structure, dispatch all cases individually
                        dispatch_cases_to_queue(
                            run, cases_to_run, effective_settings, session)
                elif execution_mode == ExecutionMode.PARALLEL:
                    # PARALLEL mode: one job per test case, scheduled with a
                    # parallelism hint so workers know they can fan out
                    # aggressively. This is the path AI agents firing many
                    # runs use for fastest feedback.
                    dispatch_parallel_jobs(
                        run, cases_to_run, effective_settings, session)
                else:
                    # CONTINUOUS mode: dispatch each case as an independent job
                    dispatch_cases_to_queue(
                        run, cases_to_run, effective_settings, session)
            else:
                # Fallback: Legacy single-engine execution (deprecated)
                dispatch_legacy_execution(
                    run, cases_to_run, effective_settings, execution_mode)

        except Exception as e:
            print(f"[Worker] Error in run {run_id}: {e}")
            import traceback
            traceback.print_exc()
            run.status = TestStatus.ERROR
            run.error_message = str(e)
            session.add(run)
            session.commit()


def _case_payload(case) -> dict:
    """Serializable job payload for one test case, incl. auth-session flags."""
    return {
        'id': case.id,
        'name': case.name,
        'steps': [
            step.dict() if hasattr(step, 'dict') else step
            for step in case.steps
        ],
        'is_auth_setup': getattr(case, 'is_auth_setup', False),
        'use_auth_session': getattr(case, 'use_auth_session', True),
    }


def _settings_payload(settings: dict) -> dict:
    """The per-job settings sub-dict shared by every dispatch path."""
    payload = {
        'headers': settings.get('headers', {}),
        'params': settings.get('params', {}),
        'allowed_domains': settings.get('allowed_domains', []),
        'domain_settings': settings.get('domain_settings', {}),
    }
    if settings.get('storage_state'):
        payload['storage_state'] = settings['storage_state']
    return payload


def _load_auth_state(project_id, session):
    """Return the project's stored Playwright storageState if fresh, else None."""
    if not project_id:
        return None
    from datetime import datetime
    from sqlmodel import select
    from app.models import AuthSession

    auth = session.exec(
        select(AuthSession).where(AuthSession.project_id == project_id)
    ).first()
    if not auth or not auth.storage_state:
        return None
    age_seconds = (datetime.utcnow() - auth.captured_at).total_seconds()
    if age_seconds > auth.max_age_minutes * 60:
        print(f"[Worker] Auth session for project {project_id} is stale "
              f"({int(age_seconds // 60)} min old) — dispatching without it")
        return None
    return auth.storage_state


def dispatch_separate_jobs(run: TestRun, execution_units: list, settings: dict, session: Session):
    """
    Dispatch jobs for SEPARATE mode via Redis stream.

    Each execution unit becomes one job:
    - Single test case → one worker runs one test
    - Sub-suite → one worker runs all tests in that sub-suite CONTINUOUSLY

    This enables hierarchical execution:
    - Parent suite in SEPARATE mode spawns parallel jobs
    - Each sub-suite runs its tests sequentially in a shared browser
    """
    import uuid
    from datetime import datetime

    print(
        f"[Worker] Dispatching {len(execution_units)} execution units for run {run.id}")

    jobs_stream = 'jobs:pending'
    job_ids = []
    total_test_count = 0

    # Ensure consumer group exists
    try:
        redis_client.xgroup_create(
            jobs_stream, 'execution-workers', id='0', mkstream=True)
    except redis.ResponseError as e:
        if 'BUSYGROUP' not in str(e):
            raise

    # Use pipeline for atomic batch insert
    pipe = redis_client.pipeline()

    for unit in execution_units:
        job_id = str(uuid.uuid4())
        job_ids.append(job_id)

        test_cases = unit['test_cases']
        total_test_count += len(test_cases)

        # Determine execution mode for this job
        # Sub-suites run their tests continuously (shared browser)
        is_continuous = unit['type'] == 'sub_suite' and len(test_cases) > 1

        if is_continuous:
            # Multi-test job: runs continuously in shared browser
            job = {
                'job_id': job_id,
                'run_id': run.id,
                'execution_mode': 'continuous',
                'unit_type': unit['type'],
                'unit_id': unit['id'],
                'unit_name': unit['name'],
                'test_cases': [_case_payload(case) for case in test_cases],
                'browser': run.browser,
                'device': run.device,
                'settings': _settings_payload(settings),
                'created_at': datetime.utcnow().isoformat(),
                'retry_count': 0
            }
        else:
            # Single test job
            case = test_cases[0]
            job = {
                'job_id': job_id,
                'run_id': run.id,
                'test_case_id': case.id,
                'test_case': _case_payload(case),
                'browser': run.browser,
                'device': run.device,
                'settings': _settings_payload(settings),
                'created_at': datetime.utcnow().isoformat(),
                'retry_count': 0
            }

        # Add job to stream
        pipe.xadd(
            jobs_stream,
            {
                'job_id': job_id,
                'run_id': str(run.id),
                'payload': json.dumps(job)
            }
        )
        # Track job in run's job set
        pipe.sadd(f'runs:{run.id}:job_ids', job_id)

    # Initialize run progress tracking
    # Track by total test cases, not jobs (for accurate progress)
    pipe.hset(f'runs:{run.id}:progress', mapping={
        'total': total_test_count,
        'completed': 0,
        'passed': 0,
        'failed': 0,
        'status': 'running'
    })

    pipe.execute()

    print(
        f"[Worker] Dispatched {len(job_ids)} jobs ({total_test_count} total tests) for run {run.id}")


def dispatch_cases_to_queue(run: TestRun, cases: list, settings: dict, session: Session):
    """
    Dispatch one Redis-stream job per test case (used for both SEPARATE and
    CONTINUOUS modes when there is no sub-suite structure to exploit).
    """
    import uuid
    from datetime import datetime

    print(f"[Worker] Dispatching {len(cases)} jobs to queue for run {run.id}")

    jobs_stream = 'jobs:pending'
    job_ids = []

    # Ensure consumer group exists
    try:
        redis_client.xgroup_create(
            jobs_stream, 'execution-workers', id='0', mkstream=True)
    except redis.ResponseError as e:
        if 'BUSYGROUP' not in str(e):
            raise

    pipe = redis_client.pipeline()

    for case in cases:
        job_id = str(uuid.uuid4())
        job_ids.append(job_id)

        job = {
            'job_id': job_id,
            'run_id': run.id,
            'test_case_id': case.id,
            'test_case': _case_payload(case),
            'browser': run.browser,
            'device': run.device,
            'settings': _settings_payload(settings),
            'created_at': datetime.utcnow().isoformat(),
            'retry_count': 0
        }

        pipe.xadd(
            jobs_stream,
            {
                'job_id': job_id,
                'run_id': str(run.id),
                'payload': json.dumps(job)
            }
        )
        pipe.sadd(f'runs:{run.id}:job_ids', job_id)

    pipe.hset(f'runs:{run.id}:progress', mapping={
        'total': len(cases),
        'completed': 0,
        'passed': 0,
        'failed': 0,
        'status': 'running'
    })

    pipe.execute()

    print(f"[Worker] Dispatched {len(job_ids)} jobs to queue for run {run.id}")


def _filter_quarantined(cases: list, session: Session) -> list:
    """Drop test cases whose FlakeRecord is currently quarantined.

    Idempotent + safe: if the flakerecord table is missing or unreachable,
    returns the original list and logs once.
    """
    if not cases:
        return cases
    try:
        from sqlmodel import select as _select
        from app.models import FlakeRecord
        case_ids = [c.id for c in cases]
        stmt = _select(FlakeRecord.test_case_id).where(
            FlakeRecord.test_case_id.in_(case_ids),
            FlakeRecord.is_quarantined == True,  # noqa: E712
        )
        quarantined = {row for row in session.exec(stmt)}
        if not quarantined:
            return cases
        filtered = [c for c in cases if c.id not in quarantined]
        skipped = len(cases) - len(filtered)
        if skipped:
            print(f"[Worker] Skipping {skipped} quarantined test case(s)")
        return filtered
    except Exception as exc:  # noqa: BLE001
        print(f"[Worker] flake filter failed (continuing without): {exc}")
        return cases


def dispatch_parallel_jobs(run: TestRun, cases: list, settings: dict, session: Session):
    """PARALLEL execution dispatch — one job per case, tagged with parallel mode.

    Behaves identically to `dispatch_cases_to_queue` from the Redis-stream
    perspective (one job per case) but tags each job with
    `execution_mode='parallel'` and a `parallelism` hint so consumers can
    schedule fan-out concurrency. PARALLEL_MAX_CONCURRENCY caps the hint
    (defaults to total cases — workers may still serialize if at capacity).
    """
    import os
    import uuid
    from datetime import datetime

    parallelism = min(
        len(cases),
        int(os.getenv("PARALLEL_MAX_CONCURRENCY", str(len(cases) or 1))),
    )
    print(
        f"[Worker] Dispatching {len(cases)} PARALLEL jobs (parallelism hint={parallelism}) for run {run.id}"
    )

    jobs_stream = 'jobs:pending'
    try:
        redis_client.xgroup_create(jobs_stream, 'execution-workers', id='0', mkstream=True)
    except redis.ResponseError as e:
        if 'BUSYGROUP' not in str(e):
            raise

    pipe = redis_client.pipeline()
    job_ids = []
    for case in cases:
        job_id = str(uuid.uuid4())
        job_ids.append(job_id)
        job = {
            'job_id': job_id,
            'run_id': run.id,
            'execution_mode': 'parallel',
            'parallelism': parallelism,
            'test_case_id': case.id,
            'test_case': _case_payload(case),
            'browser': run.browser,
            'device': run.device,
            'settings': _settings_payload(settings),
            'created_at': datetime.utcnow().isoformat(),
            'retry_count': 0,
        }
        pipe.xadd(
            jobs_stream,
            {
                'job_id': job_id,
                'run_id': str(run.id),
                'payload': json.dumps(job),
            },
        )
        pipe.sadd(f'runs:{run.id}:job_ids', job_id)

    pipe.hset(f'runs:{run.id}:progress', mapping={
        'total': len(cases),
        'completed': 0,
        'passed': 0,
        'failed': 0,
        'status': 'running',
        'execution_mode': 'parallel',
    })
    pipe.execute()
    print(f"[Worker] Dispatched {len(job_ids)} PARALLEL jobs for run {run.id}")


def dispatch_legacy_execution(run: TestRun, cases: list, settings: dict, execution_mode: ExecutionMode):
    """
    Legacy execution via single execution engine.
    Used for CONTINUOUS mode where tests share browser context.
    """
    from app.services.test_service import test_service

    # Serialize test cases
    test_cases_data = []
    for case in cases:
        test_cases_data.append({
            "id": case.id,
            "name": case.name,
            "steps": [step.dict() if hasattr(step, 'dict') else step for step in case.steps],
            "settings": settings,
        })

    # Construct callback URL
    callback_url = f"http://backend:8000/api/runs/{run.id}/webhook"

    payload = {
        "runId": run.id,
        "testCases": test_cases_data,
        "browser": run.browser,
        "device": run.device,
        "executionMode": execution_mode.value,
        "globalSettings": {
            "headers": run.request_headers or {},
            "params": run.request_params or {},
            "allowed_domains": run.allowed_domains or [],
            "domain_settings": run.domain_settings or {}
        },
        "callbackUrl": callback_url,
        "webhookSecret": _WEBHOOK_SECRET
    }

    print(f"[Worker] Dispatching legacy execution for run {run.id}")

    # Call Node.js Execution Engine
    response = requests.post(EXECUTION_ENGINE_URL, json=payload, timeout=10)

    if response.status_code in [200, 202]:
        print(f"[Worker] Legacy execution accepted for run {run.id}")
    else:
        raise Exception(
            f"Execution Engine rejected: {response.status_code} {response.text}")
