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
        "app.tasks.heartbeat_tasks",   # Beat proof-of-life (H1)
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
    "app.tasks.cleanup_tasks.purge_derived_records": "main-queue",
    "app.tasks.heartbeat_tasks.beat_heartbeat": "main-queue",
    "app.tasks.cleanup_tasks.purge_orphaned_artifacts": "main-queue",
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
        # Hourly; no-op unless some project or the instance sets a retention
        # window (per-project since workstream G2).
        'schedule': 3600.0,
    },
    'beat-heartbeat': {
        # Proof of life for celery_beat (H1). Dispatched by beat and executed by
        # a worker, so it proves beat can reach the broker — a beat process that
        # is up but cannot dispatch is the silent stall this exists to catch.
        'task': 'app.tasks.heartbeat_tasks.beat_heartbeat',
        'schedule': 30.0,
    },
    'purge-derived-records': {
        'task': 'app.tasks.cleanup_tasks.purge_derived_records',
        'schedule': 86400.0,  # Daily; no-op unless DERIVED_RETENTION_DAYS > 0
    },
    'purge-orphaned-artifacts': {
        'task': 'app.tasks.cleanup_tasks.purge_orphaned_artifacts',
        # Daily and opt-in. Lists before it deletes — see the task docstring.
        'schedule': 86400.0,
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


# ---------------------------------------------------------------------------
# Beat HA (workstream H1)
# ---------------------------------------------------------------------------
# One celery_beat with no leader election, using the default file-backed
# PersistentScheduler on a container-local path. It drains jobs:results every two
# seconds, so if it dies the entire execution pipeline stalls silently — nothing
# finalises, aggregates, schedules or expires.
#
# redbeat replaces the scheduler with a Redis-backed one that holds a lock, so
# two or more beat processes can run and exactly one is active. It is OPT-IN:
# switching it on by default would move every existing deployment's schedule
# state from a file to Redis during an upgrade, and a schedule that silently
# fails to migrate is worse than the single point of failure it replaces.
#
#   CELERY_BEAT_SCHEDULER=redbeat.RedBeatScheduler
#
# See docs/OPERATIONS.md. The heartbeat above is the complementary half: it makes
# a dead scheduler *observable* whether or not you run two of them.
import os as _os

_beat_scheduler = _os.getenv("CELERY_BEAT_SCHEDULER", "").strip()
if _beat_scheduler:
    celery_app.conf.beat_scheduler = _beat_scheduler
    # redbeat needs its own Redis URL; default it to the broker so a deployment
    # only has to set one variable.
    celery_app.conf.redbeat_redis_url = _os.getenv(
        "REDBEAT_REDIS_URL", settings.CELERY_BROKER_URL)
    # Lock TTL must exceed the longest gap between beat ticks or the lock expires
    # mid-cycle and a second beat starts firing the same schedule.
    celery_app.conf.redbeat_lock_timeout = int(
        _os.getenv("REDBEAT_LOCK_TIMEOUT", "300"))
