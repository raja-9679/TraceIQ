from celery import Celery
from celery.schedules import crontab
import ssl

from app.core.config import settings

# Celery selects TLS from the rediss:// scheme, but certificate *verification*
# is a separate option and defaults to none — so `rediss://` alone gives you an
# encrypted channel to an unverified peer. CELERY_REDIS_SSL_CERT_REQS controls
# it: "required" (default, verify) | "optional" | "none" (self-signed dev).
def _redis_ssl_options():
    if not str(settings.CELERY_BROKER_URL or "").startswith("rediss://"):
        return None
    mode = (settings.CELERY_REDIS_SSL_CERT_REQS or "required").strip().lower()
    cert_reqs = {
        "none": ssl.CERT_NONE,
        "optional": ssl.CERT_OPTIONAL,
        "required": ssl.CERT_REQUIRED,
    }.get(mode, ssl.CERT_REQUIRED)
    options = {"ssl_cert_reqs": cert_reqs}
    if settings.CELERY_REDIS_SSL_CA_CERTS:
        options["ssl_ca_certs"] = settings.CELERY_REDIS_SSL_CA_CERTS
    return options


_REDIS_SSL = _redis_ssl_options()

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
        "app.tasks.monitor_tasks",      # Synthetic-monitoring evaluation
        "app.tasks.security_tasks",     # Passive security scanning
        "app.tasks.zap_tasks",          # ZAP DAST scanning
        "app.tasks.ticket_tasks",       # Issue-tracker ticket creation
        "app.tasks.triage_tasks",       # Failure clustering / triage
        "app.tasks.report_tasks",       # Scheduled quality reports
    ]
)

celery_app.conf.task_routes = {
    "app.worker.run_test_suite": "main-queue",
    "app.tasks.webhook_tasks.process_webhook_queue": "main-queue",
    "app.tasks.cleanup_tasks.cleanup_stuck_tests": "main-queue",
    "app.tasks.cleanup_tasks.purge_old_runs": "main-queue",
    "app.tasks.cleanup_tasks.purge_old_audit_logs": "main-queue",
    "app.tasks.result_aggregator.process_job_results": "aggregator-queue",
    "app.tasks.result_aggregator.check_stale_runs": "aggregator-queue",
    "app.tasks.schedule_tasks.process_test_schedules": "main-queue",
    "app.tasks.monitor_tasks.evaluate_monitor_for_run": "main-queue",
    "app.tasks.security_tasks.scan_run_passive": "main-queue",
    "app.tasks.zap_tasks.run_zap_scan": "main-queue",
    "app.tasks.ticket_tasks.create_issue_ticket": "main-queue",
    "app.tasks.triage_tasks.cluster_run_failures": "main-queue",
    "app.tasks.report_tasks.send_scheduled_reports": "main-queue",
    "app.tasks.report_tasks.send_report_now": "main-queue"
}

# Configure Celery Beat schedule for periodic tasks
if _REDIS_SSL:
    celery_app.conf.broker_use_ssl = _REDIS_SSL
    celery_app.conf.redis_backend_use_ssl = _REDIS_SSL

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
    'purge-old-audit-logs': {
        'task': 'app.tasks.cleanup_tasks.purge_old_audit_logs',
        'schedule': 86400.0,  # Daily; no-op unless AUDIT_RETENTION_DAYS > 0
    },
    'purge-old-runs': {
        'task': 'app.tasks.cleanup_tasks.purge_old_runs',
        'schedule': 3600.0,  # Hourly; no-op unless RUN_RETENTION_DAYS > 0
    },
    'send-scheduled-reports': {
        'task': 'app.tasks.report_tasks.send_scheduled_reports',
        'schedule': 300.0,  # Every 5 min; delivers report schedules that are due
    }
}

celery_app.conf.timezone = 'UTC'

# Global task time limits — soft limit raises SoftTimeLimitExceeded so tasks
# can clean up; hard limit sends SIGKILL if the task is still running after it.
celery_app.conf.task_soft_time_limit = 3600   # 1 hour
celery_app.conf.task_time_limit = 3900        # 5 min grace period beyond soft limit
