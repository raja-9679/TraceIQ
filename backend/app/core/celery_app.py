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
        "app.tasks.result_aggregator"  # New distributed execution aggregator
    ]
)

celery_app.conf.task_routes = {
    "app.worker.run_test_suite": "main-queue",
    "app.tasks.webhook_tasks.process_webhook_queue": "main-queue",
    "app.tasks.cleanup_tasks.cleanup_stuck_tests": "main-queue",
    "app.tasks.result_aggregator.process_job_results": "aggregator-queue",
    "app.tasks.result_aggregator.check_stale_runs": "aggregator-queue"
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
        'schedule': 60.0,  # Every minute
    },
}

celery_app.conf.timezone = 'UTC'
