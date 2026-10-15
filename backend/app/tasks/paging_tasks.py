"""Run-failure paging (WP8). Monitor paging lives in monitor_tasks, on its
DOWN/RECOVERY transitions; this covers channels with `on_run_failure`.

Only runs nobody is watching page: scheduled (non-monitor), CI and webhook
triggered runs. A human clicking Run, or a coding agent iterating on a branch,
is looking at the result already — paging on-call for every red run of a work
in progress would get the integration switched off within a day. Local-worker
runs (a developer's laptop) never page.
"""
import logging

from sqlmodel import Session, create_engine

from app.core.celery_app import celery_app
from app.core.config import db_url_for, settings
from app.models import RunTrigger, TestRun, TestSchedule, TestStatus

logger = logging.getLogger(__name__)

sync_engine = create_engine(db_url_for(settings.DATABASE_URL, sync=True), echo=False)

PAGEABLE_TRIGGERS = {RunTrigger.SCHEDULE, RunTrigger.CI, RunTrigger.WEBHOOK}


def page_decision(run: TestRun, schedule_is_monitor: bool):
    """'trigger' | 'resolve' | None. Pure, for tests."""
    if not run.project_id or getattr(run, "local_worker_id", None):
        return None
    trigger = run.triggered_by
    try:
        trigger = RunTrigger(trigger)
    except ValueError:
        return None
    if trigger not in PAGEABLE_TRIGGERS or schedule_is_monitor:
        return None
    if run.status in (TestStatus.FAILED, TestStatus.ERROR):
        return "trigger"
    if run.status == TestStatus.PASSED:
        return "resolve"
    return None


@celery_app.task(name="app.tasks.paging_tasks.page_for_run", bind=True,
                 max_retries=2, default_retry_delay=30)
def page_for_run(self, run_id: int):
    from app.services import paging
    from app.tasks.monitor_tasks import _run_link, _schedule_id_from_run
    try:
        with Session(sync_engine) as session:
            run = session.get(TestRun, run_id)
            if not run:
                return
            is_monitor = False
            schedule_id = _schedule_id_from_run(run)
            if schedule_id is not None:
                schedule = session.get(TestSchedule, schedule_id)
                is_monitor = bool(schedule and schedule.is_monitor)
            action = page_decision(run, is_monitor)
            if not action:
                return
            target = run.test_case_name or run.suite_name or f"suite {run.test_suite_id}"
            where = f" [{run.region}]" if getattr(run, "region", None) else ""
            paging.page(
                session, project_id=run.project_id, source="run", action=action,
                dedup_key=paging.run_dedup_key(run.project_id, run.test_suite_id,
                                               run.test_case_id, getattr(run, "region", None)),
                summary=(f"TraceIQ run FAILED: {target}{where} — "
                         f"{run.failed_tests}/{run.total_tests} failed (run #{run.id})"
                         if action == "trigger"
                         else f"TraceIQ run passing again: {target}{where} (run #{run.id})"),
                details={"run_id": run.id, "suite": run.suite_name or "",
                         "case": run.test_case_name or "", "status": str(run.status.value),
                         "git_commit": run.git_commit or "", "git_branch": run.git_branch or "",
                         "region": getattr(run, "region", None) or ""},
                link=_run_link(run.id), run_id=run.id)
    except Exception as exc:  # noqa: BLE001
        logger.error("[Paging] run %s: %s", run_id, exc)
        raise self.retry(exc=exc)
