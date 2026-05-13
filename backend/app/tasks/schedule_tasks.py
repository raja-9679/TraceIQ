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
from croniter import croniter

async def _process_schedules_async():
    async with get_session_context() as session:
        now = datetime.utcnow()
        # Find active schedules that are due
        query = select(TestSchedule).where(
            TestSchedule.is_active == True,
            TestSchedule.next_run_at <= now
        )
        result = await session.exec(query)
        schedules = result.all()

        for schedule in schedules:
            try:
                # 1. Dispatch the run
                await _dispatch_schedule(schedule, session)
                
                # 2. Update schedule times
                cron = croniter(schedule.cron_expression, now)
                schedule.last_run_at = now
                schedule.next_run_at = cron.get_next(datetime)
                session.add(schedule)
                
            except Exception as e:
                import traceback
                print(f"Error processing schedule {schedule.id}: {e}")
                traceback.print_exc()

        await session.commit()

async def _dispatch_schedule(schedule: TestSchedule, session):
    created_runs = []
    
    # Target devices check
    target_devices = [schedule.device] if schedule.device else [None]
    target_browser = schedule.browser or "chromium"

    if schedule.test_case_id:
        case = await session.get(TestCase, schedule.test_case_id)
        if not case:
            return
            
        suite_id = case.test_suite_id
        suite_path = await test_service.get_suite_path(suite_id, session) if suite_id else "Orphaned Case"
        effective_settings = await test_service.get_effective_settings(suite_id, session) if suite_id else {}

        for target_device in target_devices:
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
                    for target_device in target_devices:
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
                for target_device in target_devices:
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
