from celery import Celery
from celery.schedules import crontab
from app.core.config import settings

celery_app = Celery(
    "worker",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.worker",
        "app.tasks.webhook_tasks",
        "app.tasks.cleanup_tasks",
        "app.tasks.result_aggregator",  # New distributed execution aggregator
        "app.tasks.schedule_tasks",     # Cron test scheduler task
        "app.tasks.analysis_tasks",     # Typed failure analysis on failed runs
        # Phase B–E task modules (were missing from include, so their tasks
        # never registered on workers):
        "app.tasks.heal_tasks",
        "app.tasks.tautology_tasks",
        "app.tasks.persona_tasks",
        "app.tasks.outbound_webhook_tasks",
    ]
)

celery_app.conf.task_routes = {
    "app.worker.run_test_suite": "main-queue",
    "app.tasks.webhook_tasks.process_webhook_queue": "main-queue",
    "app.tasks.cleanup_tasks.cleanup_stuck_tests": "main-queue",
    "app.tasks.cleanup_tasks.purge_old_runs": "main-queue",
    "app.tasks.result_aggregator.process_job_results": "aggregator-queue",
    "app.tasks.result_aggregator.check_stale_runs": "aggregator-queue",
    "app.tasks.schedule_tasks.process_test_schedules": "main-queue"
}

# Configure Celery Beat schedule for periodic tasks
celery_app.conf.beat_schedule = {
    'process-webhook-queue': {
        'task': 'app.tasks.webhook_tasks.process_webhook_queue',
        'schedule': settings.WEBHOOK_PROCESSOR_INTERVAL,
    },
    'cleanup-stuck-tests': {
        'task': 'app.tasks.cleanup_tasks.cleanup_stuck_tests',
        'schedule': 300.0,  # Every 5 minutes
    },
    # New: Result aggregator for distributed execution
    'process-job-results': {
        'task': 'app.tasks.result_aggregator.process_job_results',
        'schedule': 2.0,  # Every 2 seconds
    },
    'check-stale-runs': {
        'task': 'app.tasks.result_aggregator.check_stale_runs',
        'schedule': 300.0,  # Every 5 minutes (was 60s - too aggressive for large suites)
    },
    'process-test-schedules': {
        'task': 'app.tasks.schedule_tasks.process_test_schedules',
        'schedule': 60.0, # Run every minute
    },
    'purge-old-runs': {
        'task': 'app.tasks.cleanup_tasks.purge_old_runs',
        'schedule': 3600.0,  # Hourly; no-op unless RUN_RETENTION_DAYS > 0
    }
}

celery_app.conf.timezone = 'UTC'

# Global task time limits — soft limit raises SoftTimeLimitExceeded so tasks
# can clean up; hard limit sends SIGKILL if the task is still running after it.
celery_app.conf.task_soft_time_limit = 3600   # 1 hour
celery_app.conf.task_time_limit = 3900        # 5 min grace period beyond soft limit
