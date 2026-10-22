import asyncio
from datetime import datetime
from sqlmodel import select
from celery import shared_task
from app.core.database import get_session_context
from app.models import (
    ExecutionMode,
    RunTrigger,
    TestCase,
    TestRun,
    TestSchedule,
    TestStatus,
    TestSuite,
)
from app.services.test_service import test_service
from app.services.schedule_time import next_run_utc

async def _process_schedules_async():
    async with get_session_context() as session:
        now = datetime.utcnow()
        # An active schedule with no next_run_at (written before it was set on
        # create, or by a direct insert) is never "due" under `<= now` and so
        # would never run. Give it its next slot rather than firing at once.
        unset = (await session.exec(select(TestSchedule).where(
            TestSchedule.is_active == True,  # noqa: E712
            TestSchedule.next_run_at == None))).all()  # noqa: E711
        for schedule in unset:
            try:
                schedule.next_run_at = next_run_utc(
                    schedule.cron_expression, schedule.timezone, after=now)
                session.add(schedule)
            except Exception as e:  # noqa: BLE001 — an invalid cron stays unset
                print(f"Schedule {schedule.id} has no usable next run: {e}")
        # Find active schedules that are due
        query = select(TestSchedule).where(
            TestSchedule.is_active == True,
            TestSchedule.next_run_at <= now
        )
        result = await session.exec(query)
        schedules = result.all()

        demo_projects = await _demo_project_ids(session, {s.project_id for s in schedules})
        for schedule in schedules:
            if schedule.project_id in demo_projects:
                # A demo workspace never dispatches (its history is generated
                # and nothing in it may call out). The schedule still moves on
                # so it is not re-selected every tick.
                schedule.next_run_at = next_run_utc(
                    schedule.cron_expression, schedule.timezone, after=now)
                session.add(schedule)
                continue
            try:
                # 1. Dispatch the run
                await _dispatch_schedule(schedule, session)
                
                # 2. Update schedule times
                schedule.last_run_at = now
                schedule.next_run_at = next_run_utc(
                    schedule.cron_expression, schedule.timezone, after=now)
                session.add(schedule)
                
            except Exception as e:
                import traceback
                print(f"Error processing schedule {schedule.id}: {e}")
                traceback.print_exc()

        await session.commit()

async def _demo_project_ids(session, project_ids) -> set:
    """The subset of `project_ids` that live in a demo workspace."""
    from app.models import Project, Workspace
    ids = [p for p in project_ids if p]
    if not ids:
        return set()
    rows = (await session.exec(
        select(Project.id).join(Workspace, Workspace.id == Project.workspace_id)
        .where(Project.id.in_(ids), Workspace.is_demo == True))).all()  # noqa: E712
    return set(rows)


async def _dispatch_schedule(schedule: TestSchedule, session):
    created_runs = []

    # Recurring security scan — fires a BASELINE (never active) ZAP scan of
    # the target instead of a suite run. The same per-project gates apply as
    # for manual scans: security enabled + target host on the allowlist.
    if schedule.security_scan_target:
        from app.api.security import _host_allowed, _security_settings
        from app.models import Project, SecurityScan
        from app.tasks.zap_tasks import run_zap_scan

        project = await session.get(Project, schedule.project_id)
        sec = _security_settings(project) if project else None
        if not sec or not sec.enabled:
            print(f"[Schedule] Security scan schedule {schedule.id} skipped: security not enabled on project")
            return
        if not _host_allowed(schedule.security_scan_target, sec.allowed_domains):
            print(f"[Schedule] Security scan schedule {schedule.id} skipped: target host not on the allowlist")
            return
        scan = SecurityScan(
            project_id=schedule.project_id,
            target_url=schedule.security_scan_target,
            scan_type="baseline",
            requested_by_id=schedule.created_by_id,
        )
        session.add(scan)
        await session.commit()
        await session.refresh(scan)
        run_zap_scan.delay(scan.id)
        print(f"[Schedule] Dispatched scheduled baseline security scan {scan.id} "
              f"for schedule {schedule.id} → {schedule.security_scan_target}")
        return

    # Target devices check
    target_devices = [schedule.device] if schedule.device else [None]
    # Multi-region monitors (WP8): one run per region per device, each pinned
    # to that region's job stream. No regions = one unlabelled run, as before.
    target_regions = list(schedule.regions or []) or [None]
    target_browser = schedule.browser or "chromium"

    # Frontend matrix (WP10): a scheduled suite run fans out over the suite
    # chain's viewports / locales / colour schemes like a manual one. Not for
    # monitors: a monitor's uptime is one check per tick, and N variant runs
    # per tick would each count as a check.
    import uuid as _uuid
    from app.services import frontend_matrix
    sweep_group = _uuid.uuid4().hex

    def _slots(eff):
        variants = [None]
        if not schedule.is_monitor:
            try:
                variants = frontend_matrix.build_variants(frontend_matrix.resolve_options(None, eff or {}))
            except frontend_matrix.MatrixError as exc:
                print(f"[Schedule] {schedule.id}: ignoring invalid frontend matrix settings: {exc}")
        # devices x regions (WP8) x variants (WP10); in practice regions are
        # set on monitors and variants never are, so one axis is usually [None].
        return [(d, r, v) for d in target_devices for r in target_regions for v in variants]

    if schedule.test_case_id:
        case = await session.get(TestCase, schedule.test_case_id)
        if not case:
            return
            
        suite_id = case.test_suite_id
        suite_path = await test_service.get_suite_path(suite_id, session) if suite_id else "Orphaned Case"
        effective_settings = await test_service.get_effective_settings(suite_id, session) if suite_id else {}

        for target_device, target_region, variant in _slots(effective_settings):
            run = TestRun(
                status=TestStatus.PENDING,
                test_suite_id=suite_id,
                test_case_id=case.id,
                project_id=schedule.project_id,
                suite_name=suite_path,
                test_case_name=case.name,
                request_headers=effective_settings.get("headers", {}),
                request_params=effective_settings.get("params", {}),
                allowed_domains=effective_settings.get("allowed_domains", []),
                domain_settings=effective_settings.get("domain_settings", {}),
                browser=target_browser,
                device=target_device,
                region=target_region,
                emulation=variant,
                sweep_group=sweep_group if variant else None,
                user_id=schedule.created_by_id,  # Blame the run on the schedule creator
                triggered_by=RunTrigger.SCHEDULE,
                agent_id=f"schedule:{schedule.id}",
            )
            session.add(run)
            await session.flush()
            created_runs.append(run)
            
    elif schedule.test_suite_id:
        suite_id = schedule.test_suite_id
        suite = await session.get(TestSuite, suite_id)
        if not suite:
            return

        effective_settings = await test_service.get_effective_settings(suite_id, session)
        
        async def process_suite(s_id: int, parent_settings: dict):
            current_suite = await session.get(TestSuite, s_id)
            if not current_suite: return
            
            current_effective_settings = await test_service.get_effective_settings(s_id, session)
            suite_path = await test_service.get_suite_path(s_id, session)

            if current_suite.execution_mode == ExecutionMode.SEPARATE:
                result = await session.exec(select(TestCase).where(TestCase.test_suite_id == s_id))
                direct_cases = result.all()
                for case in direct_cases:
                    for target_device, target_region, variant in _slots(current_effective_settings):
                        run = TestRun(
                            status=TestStatus.PENDING,
                            test_suite_id=s_id,
                            test_case_id=case.id,
                            project_id=schedule.project_id,
                            suite_name=suite_path,
                            test_case_name=case.name,
                            request_headers=current_effective_settings.get("headers", {}),
                            request_params=current_effective_settings.get("params", {}),
                            allowed_domains=current_effective_settings.get("allowed_domains", []),
                            domain_settings=current_effective_settings.get("domain_settings", {}),
                            browser=target_browser,
                            device=target_device,
                            region=target_region,
                            emulation=variant,
                            sweep_group=sweep_group if variant else None,
                            user_id=schedule.created_by_id,
                            triggered_by=RunTrigger.SCHEDULE,
                            agent_id=f"schedule:{schedule.id}",
                        )
                        session.add(run)
                        await session.flush()
                        created_runs.append(run)
                        
                result = await session.exec(select(TestSuite).where(TestSuite.parent_id == s_id))
                sub_modules = result.all()
                for sub in sub_modules:
                    await process_suite(sub.id, current_effective_settings)
            else:
                for target_device, target_region, variant in _slots(current_effective_settings):
                    run = TestRun(
                        status=TestStatus.PENDING,
                        test_suite_id=s_id,
                        test_case_id=None,
                        project_id=schedule.project_id,
                        suite_name=suite_path,
                        test_case_name=None,
                        request_headers=current_effective_settings.get("headers", {}),
                        request_params=current_effective_settings.get("params", {}),
                        allowed_domains=current_effective_settings.get("allowed_domains", []),
                        domain_settings=current_effective_settings.get("domain_settings", {}),
                        browser=target_browser,
                        device=target_device,
                        region=target_region,
                        emulation=variant,
                        sweep_group=sweep_group if variant else None,
                        user_id=schedule.created_by_id,
                        triggered_by=RunTrigger.SCHEDULE,
                        agent_id=f"schedule:{schedule.id}",
                    )
                    session.add(run)
                    await session.flush()
                    created_runs.append(run)
                    
                async def find_and_process_separate_descendants(p_id):
                    result = await session.exec(select(TestSuite).where(TestSuite.parent_id == p_id))
                    subs = result.all()
                    for sub in subs:
                        if sub.execution_mode == ExecutionMode.SEPARATE:
                            await process_suite(sub.id, current_effective_settings)
                        else:
                            await find_and_process_separate_descendants(sub.id)

                await find_and_process_separate_descendants(s_id)

        await process_suite(suite_id, effective_settings)

    await session.commit()
    for r in created_runs:
        await session.refresh(r)

    # Queue tasks to celery worker using existing dispatch method
    from app.worker import run_test_suite
    for run in created_runs:
        try:
            run_test_suite.delay(run.id)
        except Exception as e:
            print(f"Failed to queue scheduled run {run.id}: {e}")

@shared_task(name="app.tasks.schedule_tasks.process_test_schedules")
def process_test_schedules():
    """
    Celery task run periodically (e.g. every minute) to check and fire due test schedules.
    """
    import logging
    logger = logging.getLogger(__name__)
    logger.info("Checking for due test schedules...")
    
    try:
        # Run async function in synchronous celery task
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_process_schedules_async())
        else:
            loop.run_until_complete(_process_schedules_async())
    except Exception as e:
        logger.error(f"Error in process_test_schedules: {e}")
