from typing import List, Optional, Dict, Any
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, or_, and_
from sqlalchemy import delete as sql_delete
from sqlalchemy.orm import selectinload
from app.models import (
    TestSuite, TestCase, TestRun, TestCaseResult,
    AuditLog, ExecutionMode, TestStatus
)
from app.core.storage import minio_client

from sqlmodel import Session, select


def normalize_steps(steps: Optional[List[Any]]) -> List[Any]:
    """Ensure every step dict has a non-empty `id`.

    Agent-supplied steps (proposal queue, imports) routinely omit ids; the
    read models (TestCaseRead.steps) validate against TestStep, so a stored
    step without an id used to 500 every suite/case read that included it.
    Mutates nothing: returns new dicts for steps that needed an id.
    """
    import uuid

    normalized = []
    for step in steps or []:
        if isinstance(step, dict) and not step.get("id"):
            step = {**step, "id": f"step-{uuid.uuid4().hex[:8]}"}
        normalized.append(step)
    return normalized


def _normalize_domain(d) -> Optional[Dict]:
    """Normalize an allowed_domains entry to a dict with a 'domain' key."""
    if not d:
        return None
    if isinstance(d, str):
        return {"domain": d, "headers": True, "params": False}
    if isinstance(d, dict) and "domain" not in d:
        return None
    return d


def _merge_settings(parent: Dict[str, Any], child: Dict[str, Any]) -> Dict[str, Any]:
    """Merge child settings on top of parent settings (child wins on conflict)."""
    merged_headers = {**parent.get("headers", {}), **child.get("headers", {})}
    merged_params = {**parent.get("params", {}), **child.get("params", {})}

    domains_map: Dict[str, Any] = {}
    for d in parent.get("allowed_domains", []):
        norm = _normalize_domain(d)
        if norm:
            domains_map[norm["domain"]] = norm
    for d in child.get("allowed_domains", []):
        norm = _normalize_domain(d)
        if norm:
            domains_map[norm["domain"]] = norm

    merged_domain_settings = {**parent.get("domain_settings", {})}
    for domain, ds in child.get("domain_settings", {}).items():
        if domain in merged_domain_settings:
            merged_domain_settings[domain] = {
                "headers": {**merged_domain_settings[domain].get("headers", {}), **ds.get("headers", {})},
                "params": {**merged_domain_settings[domain].get("params", {}), **ds.get("params", {})}
            }
        else:
            merged_domain_settings[domain] = ds

    return {
        "headers": merged_headers,
        "params": merged_params,
        "allowed_domains": list(domains_map.values()),
        "domain_settings": merged_domain_settings,
        # Execution matrix: a child that sets its own list replaces the
        # parent's wholesale (no union) — "my module runs on exactly these".
        "browsers": child.get("browsers") or parent.get("browsers") or [],
        "devices": child.get("devices") or parent.get("devices") or [],
    }


class TestService:
    @staticmethod
    async def get_effective_settings(suite_id: int, session: AsyncSession) -> Dict[str, Any]:
        suite = await session.get(TestSuite, suite_id)
        if not suite:
            return {"headers": {}, "params": {}, "allowed_domains": [], "domain_settings": {}}

        current = suite.settings or {}

        if suite.inherit_settings and suite.parent_id:
            parent = await TestService.get_effective_settings(suite.parent_id, session)
            return _merge_settings(parent, current)

        return {
            "headers": current.get("headers", {}),
            "params": current.get("params", {}),
            "allowed_domains": current.get("allowed_domains", []),
            "domain_settings": current.get("domain_settings", {}),
            "browsers": current.get("browsers", []),
            "devices": current.get("devices", []),
        }

    @staticmethod
    async def get_suite_path(suite_id: int, session: AsyncSession) -> str:
        suite = await session.get(TestSuite, suite_id)
        if not suite:
            return ""
        if suite.parent_id:
            parent_path = await TestService.get_suite_path(suite.parent_id, session)
            return f"{parent_path} / {suite.name}" if parent_path else suite.name
        return suite.name

    @staticmethod
    def get_effective_settings_sync(suite_id: int, session: Session) -> Dict[str, Any]:
        suite = session.get(TestSuite, suite_id)
        if not suite:
            return {"headers": {}, "params": {}, "allowed_domains": [], "domain_settings": {}}

        current = suite.settings or {}

        if suite.inherit_settings and suite.parent_id:
            parent = TestService.get_effective_settings_sync(suite.parent_id, session)
            return _merge_settings(parent, current)

        return {
            "headers": current.get("headers", {}),
            "params": current.get("params", {}),
            "allowed_domains": current.get("allowed_domains", []),
            "domain_settings": current.get("domain_settings", {}),
            "browsers": current.get("browsers", []),
            "devices": current.get("devices", []),
        }

    @staticmethod
    def collect_cases_recursive_sync(suite_id: int, session: Session) -> List[TestCase]:
        cases = []
        suite = session.get(TestSuite, suite_id)
        if suite:
            cases.extend(suite.test_cases)

        result = session.exec(select(TestSuite).where(
            TestSuite.parent_id == suite_id))
        subs = result.all()
        for sub in subs:
            if sub.execution_mode == ExecutionMode.SEPARATE:
                continue
            cases.extend(
                TestService.collect_cases_recursive_sync(sub.id, session))
        return cases

    @staticmethod
    def collect_execution_units_sync(suite_id: int, session: Session) -> List[Dict[str, Any]]:
        """
        Collect execution units for hybrid execution (SEPARATE at suite level, CONTINUOUS within).

        For a SEPARATE mode suite:
        - Direct test cases become individual jobs
        - Each sub-suite becomes one job (with all its test cases running CONTINUOUSLY)

        Returns list of execution units, each containing:
        - 'type': 'test_case' or 'sub_suite'
        - 'id': test case ID or sub-suite ID
        - 'name': name for logging
        - 'test_cases': list of test cases to run (for sub_suite type, multiple cases)
        """
        suite = session.get(TestSuite, suite_id)
        if not suite:
            return []

        units = []

        # Direct test cases of this suite become individual jobs
        for case in suite.test_cases:
            units.append({
                'type': 'test_case',
                'id': case.id,
                'name': case.name,
                'test_cases': [case]
            })

        # Sub-suites: each becomes one job with all its cases
        result = session.exec(select(TestSuite).where(
            TestSuite.parent_id == suite_id))
        sub_suites = result.all()

        for sub in sub_suites:
            # Collect all cases within this sub-suite (recursively if needed)
            sub_cases = TestService.collect_cases_recursive_sync(
                sub.id, session)
            if sub_cases:
                units.append({
                    'type': 'sub_suite',
                    'id': sub.id,
                    'name': sub.name,
                    'test_cases': sub_cases,
                    'settings': sub.settings
                })

        return units

    @staticmethod
    async def count_recursive_items(suite_id: int, session: AsyncSession):
        result = await session.exec(select(TestCase).where(TestCase.test_suite_id == suite_id))
        direct_cases = len(result.all())

        result = await session.exec(select(TestSuite).where(TestSuite.parent_id == suite_id))
        direct_subs = result.all()

        total_cases = direct_cases
        total_subs = len(direct_subs)

        for sub in direct_subs:
            sub_cases, sub_subs = await TestService.count_recursive_items(sub.id, session)
            total_cases += sub_cases
            total_subs += sub_subs

        return total_cases, total_subs

    @staticmethod
    async def recursive_delete_suite(suite_id: int, session: AsyncSession):
        # 1. Delete runs (and their results) attached to this suite directly
        run_rows = await session.exec(
            select(TestRun.id).where(TestRun.test_suite_id == suite_id)
        )
        suite_run_ids = run_rows.all()
        if suite_run_ids:
            await session.exec(
                sql_delete(TestCaseResult).where(
                    TestCaseResult.test_run_id.in_(suite_run_ids)
                )
            )
            for run_id in suite_run_ids:
                try:
                    minio_client.delete_run_artifacts(run_id)
                except Exception as exc:  # noqa: BLE001
                    print(f"[DeleteSuite] MinIO cleanup for run {run_id} failed (continuing): {exc}")
            await session.exec(
                sql_delete(TestRun).where(TestRun.id.in_(suite_run_ids))
            )

        # 2. Delete cases (and their associated runs/results) belonging to this suite
        case_rows = await session.exec(
            select(TestCase.id).where(TestCase.test_suite_id == suite_id)
        )
        case_ids = case_rows.all()
        if case_ids:
            case_run_rows = await session.exec(
                select(TestRun.id).where(TestRun.test_case_id.in_(case_ids))
            )
            case_run_ids = case_run_rows.all()
            if case_run_ids:
                await session.exec(
                    sql_delete(TestCaseResult).where(
                        TestCaseResult.test_run_id.in_(case_run_ids)
                    )
                )
                for run_id in case_run_ids:
                    try:
                        minio_client.delete_run_artifacts(run_id)
                    except Exception as exc:  # noqa: BLE001
                        print(f"[DeleteSuite] MinIO cleanup for run {run_id} failed (continuing): {exc}")
                await session.exec(
                    sql_delete(TestRun).where(TestRun.id.in_(case_run_ids))
                )

            # Phase B/C/D dependents keyed by case id (mirrors the
            # delete_case handler fix). Same idea: bulk-delete the
            # rows that have FKs into testcase before we can drop the
            # cases themselves.
            from app.models import (
                VisualBaseline,
                FlakeRecord,
                SelectorHealProposal,
                CaseProposal,
                UserTestCaseAccess,
            )
            await session.exec(sql_delete(VisualBaseline).where(VisualBaseline.test_case_id.in_(case_ids)))
            await session.exec(sql_delete(FlakeRecord).where(FlakeRecord.test_case_id.in_(case_ids)))
            await session.exec(sql_delete(SelectorHealProposal).where(SelectorHealProposal.test_case_id.in_(case_ids)))
            await session.exec(sql_delete(CaseProposal).where(CaseProposal.target_case_id.in_(case_ids)))
            await session.exec(sql_delete(UserTestCaseAccess).where(UserTestCaseAccess.test_case_id.in_(case_ids)))

            await session.exec(
                sql_delete(TestCase).where(TestCase.id.in_(case_ids))
            )

        # 3. Recurse for sub-modules
        result = await session.exec(
            select(TestSuite).where(TestSuite.parent_id == suite_id)
        )
        sub_modules = result.all()
        for sub in sub_modules:
            await TestService.recursive_delete_suite(sub.id, session)

        # 4. Delete Suite
        suite = await session.get(TestSuite, suite_id)
        if suite:
            await session.delete(suite)

    @staticmethod
    async def collect_cases_recursive(suite_id: int, session: AsyncSession) -> List[TestCase]:
        cases = []
        # Explicit query to avoid async lazy load issues
        result = await session.exec(select(TestCase).where(TestCase.test_suite_id == suite_id))
        cases.extend(result.all())

        result = await session.exec(select(TestSuite).where(TestSuite.parent_id == suite_id))
        subs = result.all()
        for sub in subs:
            if sub.execution_mode == ExecutionMode.SEPARATE:
                continue
            cases.extend(await TestService.collect_cases_recursive(sub.id, session))
        return cases

    @staticmethod
    async def process_test_run_result(run_id: int, result_data: Dict[str, Any], session: AsyncSession):
        run = await session.get(TestRun, run_id)
        if not run:
            print(f"Error: Run {run_id} not found during result processing")
            return

        webhook_type = result_data.get("type", "complete")

        # Handle progressive updates (individual test completions)
        if webhook_type == "progress":
            await TestService._process_progressive_update(run, result_data, session)
            return

        # Handle final completion (existing logic)
        await TestService._process_final_result(run, result_data, session)

    @staticmethod
    async def _process_progressive_update(run: TestRun, result_data: Dict[str, Any], session: AsyncSession):
        """Process a single test case completion in real-time"""
        test_case_id = result_data.get("test_case_id")
        test_name = result_data.get("test_name")

        # Check if result already exists (idempotency)
        existing = await session.exec(
            select(TestCaseResult).where(
                TestCaseResult.test_run_id == run.id,
                TestCaseResult.test_name == test_name
            )
        )
        existing_result = existing.first()

        status = TestStatus.PASSED if result_data.get(
            "status") == "passed" else TestStatus.FAILED

        if existing_result:
            # Update existing
            existing_result.status = status
            existing_result.duration_ms = result_data.get("duration_ms", 0)
            existing_result.error_message = result_data.get("error")
            session.add(existing_result)
        else:
            # Create new
            test_result = TestCaseResult(
                test_run_id=run.id,
                test_name=test_name,
                status=status,
                duration_ms=result_data.get("duration_ms", 0),
                error_message=result_data.get("error")
            )
            session.add(test_result)

        # Update run progress counts
        results = await session.exec(
            select(TestCaseResult).where(TestCaseResult.test_run_id == run.id)
        )
        all_results = results.all()

        run.passed_tests = sum(
            1 for r in all_results if r.status == TestStatus.PASSED)
        run.failed_tests = sum(
            1 for r in all_results if r.status == TestStatus.FAILED)
        run.status = TestStatus.RUNNING  # Keep as running until final webhook

        session.add(run)
        await session.commit()

        # Publish real-time update
        try:
            from app.core.redis import RedisClient
            import json
            redis = RedisClient.get_instance()
            payload = {
                "run_id": run.id,
                "type": "progress",
                "status": run.status,
                "passed_tests": run.passed_tests,
                "failed_tests": run.failed_tests,
                "total_tests": run.total_tests,
                "latest_test": test_name,
                "latest_status": result_data.get("status")
            }
            await redis.publish(f"run:{run.id}", json.dumps(payload))
            print(
                f"[Progress] Published update for run {run.id}: {test_name} ({result_data.get('status')})")
        except Exception as e:
            print(f"Failed to publish progress event for run {run.id}: {e}")

    @staticmethod
    async def _process_final_result(run: TestRun, result_data: Dict[str, Any], session: AsyncSession):
        """Process final completion with full results"""
        # Update Run Fields
        run.status = TestStatus.PASSED if result_data.get(
            "status") == "passed" else TestStatus.FAILED
        run.duration_ms = result_data.get("duration_ms")
        run.error_message = result_data.get("error")
        run.trace_url = result_data.get("trace")
        run.video_url = result_data.get("video")
        run.screenshots = result_data.get("screenshots", [])
        run.response_status = result_data.get("response_status")
        run.request_headers = result_data.get("request_headers")
        run.response_headers = result_data.get("response_headers")
        run.network_events = result_data.get("network_events")
        run.execution_log = result_data.get("execution_log")

        # Calculate Stats
        test_results = result_data.get("results", [])
        results_by_id = {}
        results_by_name = {}
        for res in test_results:
            if res.get("test_case_id"):
                results_by_id[res.get("test_case_id")] = res
            results_by_name[res.get("test_name")] = res

        passed_count = 0
        failed_count = 0

        # Determine expected cases
        if run.test_case_id:
            case = await session.get(TestCase, run.test_case_id)
            cases_to_run = [case] if case else []
        else:
            cases_to_run = await TestService.collect_cases_recursive(run.test_suite_id, session)

        # Clear existing results (idempotency) — bulk delete, not row-by-row
        await session.exec(
            sql_delete(TestCaseResult).where(TestCaseResult.test_run_id == run.id)
        )

        for case in cases_to_run:
            case_res = results_by_id.get(
                case.id) or results_by_name.get(case.name)

            if case_res:
                status = TestStatus.PASSED if case_res.get(
                    "status") == "passed" else TestStatus.FAILED
                if status == TestStatus.PASSED:
                    passed_count += 1
                else:
                    failed_count += 1

                test_result = TestCaseResult(
                    test_run_id=run.id,
                    test_name=case.name,
                    status=status,
                    duration_ms=case_res.get("duration_ms", 0),
                    error_message=case_res.get("error"),
                    trace_url=case_res.get("trace"),
                    video_url=case_res.get("video"),
                    screenshots=case_res.get("screenshots", []),
                    response_status=case_res.get("response_status"),
                    response_headers=case_res.get("response_headers"),
                    response_body=case_res.get("response_body"),
                    request_headers=case_res.get("request_headers"),
                    request_body=case_res.get("request_body"),
                    request_url=case_res.get("request_url"),
                    request_method=case_res.get("request_method"),
                    request_params=case_res.get("request_params")
                )
                session.add(test_result)
            else:
                # Skipped/Error
                failed_count += 1
                test_result = TestCaseResult(
                    test_run_id=run.id,
                    test_name=case.name,
                    status=TestStatus.FAILED,
                    duration_ms=0,
                    error_message="Test execution skipped or crashed before completion"
                )
                session.add(test_result)

        run.total_tests = len(cases_to_run)
        run.passed_tests = passed_count
        run.failed_tests = failed_count

        if failed_count > 0:
            run.status = TestStatus.FAILED
        elif run.error_message:
            run.status = TestStatus.FAILED
        else:
            run.status = TestStatus.PASSED

        session.add(run)
        await session.commit()

        # Publish Real-time Update
        try:
            from app.core.redis import RedisClient
            import json
            redis = RedisClient.get_instance()
            payload = {
                "run_id": run.id,
                "type": "complete",
                "status": run.status,
                "passed_tests": run.passed_tests,
                "failed_tests": run.failed_tests,
                "total_tests": run.total_tests,
                "video_url": run.video_url
            }
            await redis.publish(f"run:{run.id}", json.dumps(payload))
            print(f"[Complete] Published final update for run {run.id}")
        except Exception as e:
            print(f"Failed to publish redis event for run {run.id}: {e}")


test_service = TestService()
