"""
Job Dispatcher Service - Manages job queue for distributed test execution

This service:
1. Creates individual jobs per test case (for PARALLEL/SEPARATE modes)
2. Tracks run progress via Redis
3. Provides APIs for job status and progress
"""
import json
import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime
import redis.asyncio as redis
from app.core.config import settings
from app.models import TestCase, TestRun, ExecutionMode

# Redis configuration
JOBS_STREAM = 'jobs:pending'
RESULTS_STREAM = 'jobs:results'
CONSUMER_GROUP = 'execution-workers'


class JobDispatcher:
    def __init__(self):
        self._redis: Optional[redis.Redis] = None

    async def get_redis(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis.from_url(
                settings.CELERY_BROKER_URL,
                decode_responses=True
            )
        return self._redis

    async def initialize(self):
        """Initialize Redis streams and consumer groups"""
        r = await self.get_redis()

        # Create consumer group for jobs stream (idempotent)
        try:
            await r.xgroup_create(JOBS_STREAM, CONSUMER_GROUP, id='0', mkstream=True)
        except redis.ResponseError as e:
            if 'BUSYGROUP' not in str(e):
                raise

        # Create consumer group for results stream
        try:
            await r.xgroup_create(RESULTS_STREAM, 'result-processors', id='0', mkstream=True)
        except redis.ResponseError as e:
            if 'BUSYGROUP' not in str(e):
                raise

    async def dispatch_parallel_run(
        self,
        run: TestRun,
        test_cases: List[TestCase],
        effective_settings: Dict[str, Any]
    ) -> List[str]:
        """
        Dispatch individual jobs for parallel execution.
        Each test case gets its own job for complete isolation.

        Returns list of job IDs.
        """
        r = await self.get_redis()
        job_ids = []

        # Prepare all jobs
        jobs_data = []
        for case in test_cases:
            job_id = str(uuid.uuid4())
            job_ids.append(job_id)

            job = {
                'job_id': job_id,
                'run_id': run.id,
                'test_case_id': case.id,
                'test_case': {
                    'id': case.id,
                    'name': case.name,
                    'steps': [
                        step.dict() if hasattr(step, 'dict') else step
                        for step in case.steps
                    ]
                },
                'browser': run.browser,
                'device': run.device,
                'settings': {
                    'headers': effective_settings.get('headers', {}),
                    'params': effective_settings.get('params', {}),
                    'allowed_domains': effective_settings.get('allowed_domains', []),
                    'domain_settings': effective_settings.get('domain_settings', {})
                },
                'created_at': datetime.utcnow().isoformat(),
                'retry_count': 0
            }
            jobs_data.append((job_id, job))

        # Use pipeline for atomic batch insert
        pipe = r.pipeline()

        # Add all jobs to stream
        for job_id, job in jobs_data:
            pipe.xadd(
                JOBS_STREAM,
                {
                    'job_id': job_id,
                    'run_id': str(run.id),
                    'payload': json.dumps(job)
                }
            )
            # Track job in run's job set
            pipe.sadd(f'runs:{run.id}:job_ids', job_id)

        # Initialize run progress tracking
        pipe.hset(f'runs:{run.id}:progress', mapping={
            'total': len(test_cases),
            'completed': 0,
            'passed': 0,
            'failed': 0,
            'status': 'running'
        })

        await pipe.execute()

        print(
            f"[JobDispatcher] Dispatched {len(job_ids)} jobs for run {run.id}")
        return job_ids

    async def dispatch_continuous_run(
        self,
        run: TestRun,
        test_cases: List[TestCase],
        effective_settings: Dict[str, Any]
    ) -> str:
        """
        Dispatch a single job containing all test cases for continuous execution.
        Tests share browser context and execute sequentially.

        Returns single job ID.
        """
        r = await self.get_redis()
        job_id = str(uuid.uuid4())

        # Build job with all test cases
        job = {
            'job_id': job_id,
            'run_id': run.id,
            'execution_mode': 'continuous',
            'test_cases': [
                {
                    'id': case.id,
                    'name': case.name,
                    'steps': [
                        step.dict() if hasattr(step, 'dict') else step
                        for step in case.steps
                    ]
                }
                for case in test_cases
            ],
            'browser': run.browser,
            'device': run.device,
            'settings': {
                'headers': effective_settings.get('headers', {}),
                'params': effective_settings.get('params', {}),
                'allowed_domains': effective_settings.get('allowed_domains', []),
                'domain_settings': effective_settings.get('domain_settings', {})
            },
            'created_at': datetime.utcnow().isoformat(),
            'retry_count': 0
        }

        pipe = r.pipeline()

        # Add job to stream
        pipe.xadd(
            JOBS_STREAM,
            {
                'job_id': job_id,
                'run_id': str(run.id),
                'payload': json.dumps(job)
            }
        )
        pipe.sadd(f'runs:{run.id}:job_ids', job_id)

        # Initialize progress (1 job for continuous)
        pipe.hset(f'runs:{run.id}:progress', mapping={
            'total': 1,
            'completed': 0,
            'passed': 0,
            'failed': 0,
            'status': 'running'
        })

        await pipe.execute()

        print(
            f"[JobDispatcher] Dispatched continuous job {job_id} for run {run.id}")
        return job_id

    async def get_run_progress(self, run_id: int) -> Optional[Dict[str, Any]]:
        """Get current progress for a test run"""
        r = await self.get_redis()
        progress = await r.hgetall(f'runs:{run_id}:progress')

        if not progress:
            return None

        return {
            'total': int(progress.get('total', 0)),
            'completed': int(progress.get('completed', 0)),
            'passed': int(progress.get('passed', 0)),
            'failed': int(progress.get('failed', 0)),
            'status': progress.get('status', 'unknown')
        }

    async def get_queue_stats(self) -> Dict[str, int]:
        """Get queue statistics"""
        r = await self.get_redis()

        pending = await r.xlen(JOBS_STREAM)
        results = await r.xlen(RESULTS_STREAM)

        # Get pending count from consumer group info
        processing = 0
        try:
            groups = await r.xinfo_groups(JOBS_STREAM)
            for group in groups:
                if isinstance(group, dict):
                    processing += group.get('pending', 0)
        except Exception:
            pass

        return {
            'pending': pending,
            'processing': processing,
            'results': results
        }

    async def cancel_run(self, run_id: int) -> int:
        """
        Cancel all pending jobs for a run.
        Returns number of jobs cancelled.
        """
        r = await self.get_redis()

        # Get job IDs for this run
        job_ids = await r.smembers(f'runs:{run_id}:job_ids')

        if not job_ids:
            return 0

        # Mark run as cancelled
        await r.hset(f'runs:{run_id}:progress', 'status', 'cancelled')

        # Note: Jobs already claimed by workers will still complete
        # This just prevents new claims and marks run as cancelled

        print(f"[JobDispatcher] Cancelled run {run_id} ({len(job_ids)} jobs)")
        return len(job_ids)

    async def cleanup_run(self, run_id: int):
        """Clean up Redis keys for a completed run"""
        r = await self.get_redis()

        # Delete run tracking keys
        await r.delete(
            f'runs:{run_id}:job_ids',
            f'runs:{run_id}:progress'
        )

    async def close(self):
        """Close Redis connection"""
        if self._redis:
            await self._redis.close()
            self._redis = None


# Singleton instance
job_dispatcher = JobDispatcher()
