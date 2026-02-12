from typing import List, Optional, Dict, Any
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, or_, and_
from sqlalchemy.orm import selectinload
from app.models import (
    TestSuite, TestCase, TestRun, TestCaseResult,
    AuditLog, ExecutionMode, TestStatus
)
from app.core.storage import minio_client

from sqlmodel import Session, select


class TestService:
    @staticmethod
    async def get_effective_settings(suite_id: int, session: AsyncSession) -> Dict[str, Any]:
        suite = await session.get(TestSuite, suite_id)
        if not suite:
            return {"headers": {}, "params": {}, "allowed_domains": [], "domain_settings": {}}

        current_settings = suite.settings or {"headers": {}, "params": {}}

        if suite.inherit_settings and suite.parent_id:
            parent_settings = await TestService.get_effective_settings(suite.parent_id, session)

            merged_headers = {
                **parent_settings.get("headers", {}), **current_settings.get("headers", {})}
            merged_params = {
                **parent_settings.get("params", {}), **current_settings.get("params", {})}

            def normalize_domain(d):
                if not d:
                    return None
                if isinstance(d, str):
                    return {"domain": d, "headers": True, "params": False}
                if isinstance(d, dict) and "domain" not in d:
                    return None
                return d

            merged_domains_map = {}
            for d in parent_settings.get("allowed_domains", []):
                norm = normalize_domain(d)
                if norm:
                    merged_domains_map[norm["domain"]] = norm
            for d in current_settings.get("allowed_domains", []):
                norm = normalize_domain(d)
                if norm:
                    merged_domains_map[norm["domain"]] = norm

            merged_domains = list(merged_domains_map.values())

            parent_domain_settings = parent_settings.get("domain_settings", {})
            current_domain_settings = current_settings.get(
                "domain_settings", {})
            merged_domain_settings = {**parent_domain_settings}
            for domain, settings in current_domain_settings.items():
                if domain in merged_domain_settings:
                    merged_domain_settings[domain] = {
                        "headers": {**merged_domain_settings[domain].get("headers", {}), **settings.get("headers", {})},
                        "params": {**merged_domain_settings[domain].get("params", {}), **settings.get("params", {})}
                    }
                else:
                    merged_domain_settings[domain] = settings

            return {
                "headers": merged_headers,
                "params": merged_params,
                "allowed_domains": merged_domains,
                "domain_settings": merged_domain_settings
            }

        return {
            "headers": current_settings.get("headers", {}),
            "params": current_settings.get("params", {}),
            "allowed_domains": current_settings.get("allowed_domains", []),
            "domain_settings": current_settings.get("domain_settings", {})
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

        current_settings = suite.settings or {"headers": {}, "params": {}}

        if suite.inherit_settings and suite.parent_id:
            parent_settings = TestService.get_effective_settings_sync(
                suite.parent_id, session)

            merged_headers = {
                **parent_settings.get("headers", {}), **current_settings.get("headers", {})}
            merged_params = {
                **parent_settings.get("params", {}), **current_settings.get("params", {})}

            def normalize_domain(d):
                if not d:
                    return None
                if isinstance(d, str):
                    return {"domain": d, "headers": True, "params": False}
                if isinstance(d, dict) and "domain" not in d:
                    return None
                return d

            merged_domains_map = {}
            for d in parent_settings.get("allowed_domains", []):
                norm = normalize_domain(d)
                if norm:
                    merged_domains_map[norm["domain"]] = norm
            for d in current_settings.get("allowed_domains", []):
                norm = normalize_domain(d)
                if norm:
                    merged_domains_map[norm["domain"]] = norm

            merged_domains = list(merged_domains_map.values())

            parent_domain_settings = parent_settings.get("domain_settings", {})
            current_domain_settings = current_settings.get(
                "domain_settings", {})
            merged_domain_settings = {**parent_domain_settings}
            for domain, settings in current_domain_settings.items():
                if domain in merged_domain_settings:
                    merged_domain_settings[domain] = {
                        "headers": {**merged_domain_settings[domain].get("headers", {}), **settings.get("headers", {})},
                        "params": {**merged_domain_settings[domain].get("params", {}), **settings.get("params", {})}
                    }
                else:
                    merged_domain_settings[domain] = settings

            return {
                "headers": merged_headers,
                "params": merged_params,
                "allowed_domains": merged_domains,
                "domain_settings": merged_domain_settings
            }

        return {
            "headers": current_settings.get("headers", {}),
            "params": current_settings.get("params", {}),
            "allowed_domains": current_settings.get("allowed_domains", []),
            "domain_settings": current_settings.get("domain_settings", {})
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
        # 1. Delete Runs
        result = await session.exec(select(TestRun).where(TestRun.test_suite_id == suite_id))
        runs = result.all()
        for run in runs:
            # Delete results
            results = await session.exec(select(TestCaseResult).where(TestCaseResult.test_run_id == run.id))
            for res in results.all():
                await session.delete(res)

            minio_client.delete_run_artifacts(run.id)
            await session.delete(run)

        # 2. Delete Cases
        result = await session.exec(select(TestCase).where(TestCase.test_suite_id == suite_id))
        cases = result.all()
        for case in cases:
            result_runs = await session.exec(select(TestRun).where(TestRun.test_case_id == case.id))
            for run in result_runs.all():
                results = await session.exec(select(TestCaseResult).where(TestCaseResult.test_run_id == run.id))
                for res in results.all():
                    await session.delete(res)

                minio_client.delete_run_artifacts(run.id)
                await session.delete(run)
            await session.delete(case)

        # 3. Recurse for sub-modules
        result = await session.exec(select(TestSuite).where(TestSuite.parent_id == suite_id))
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

        # Clear existing results (idempotency)
        existing_results = await session.exec(select(TestCaseResult).where(TestCaseResult.test_run_id == run.id))
        for res in existing_results.all():
            await session.delete(res)

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
