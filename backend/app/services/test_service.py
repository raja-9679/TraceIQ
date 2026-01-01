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
            
            merged_headers = {**parent_settings.get("headers", {}), **current_settings.get("headers", {})}
            merged_params = {**parent_settings.get("params", {}), **current_settings.get("params", {})}
            
            def normalize_domain(d):
                if not d: return None
                if isinstance(d, str): return {"domain": d, "headers": True, "params": False}
                if isinstance(d, dict) and "domain" not in d: return None
                return d

            merged_domains_map = {}
            for d in parent_settings.get("allowed_domains", []):
                norm = normalize_domain(d)
                if norm: merged_domains_map[norm["domain"]] = norm
            for d in current_settings.get("allowed_domains", []):
                norm = normalize_domain(d)
                if norm: merged_domains_map[norm["domain"]] = norm
                
            merged_domains = list(merged_domains_map.values())
            
            parent_domain_settings = parent_settings.get("domain_settings", {})
            current_domain_settings = current_settings.get("domain_settings", {})
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
            parent_settings = TestService.get_effective_settings_sync(suite.parent_id, session)
            
            merged_headers = {**parent_settings.get("headers", {}), **current_settings.get("headers", {})}
            merged_params = {**parent_settings.get("params", {}), **current_settings.get("params", {})}
            
            def normalize_domain(d):
                if not d: return None
                if isinstance(d, str): return {"domain": d, "headers": True, "params": False}
                if isinstance(d, dict) and "domain" not in d: return None
                return d

            merged_domains_map = {}
            for d in parent_settings.get("allowed_domains", []):
                norm = normalize_domain(d)
                if norm: merged_domains_map[norm["domain"]] = norm
            for d in current_settings.get("allowed_domains", []):
                norm = normalize_domain(d)
                if norm: merged_domains_map[norm["domain"]] = norm
                
            merged_domains = list(merged_domains_map.values())
            
            parent_domain_settings = parent_settings.get("domain_settings", {})
            current_domain_settings = current_settings.get("domain_settings", {})
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
            
        result = session.exec(select(TestSuite).where(TestSuite.parent_id == suite_id))
        subs = result.all()
        for sub in subs:
            if sub.execution_mode == ExecutionMode.SEPARATE:
                continue
            cases.extend(TestService.collect_cases_recursive_sync(sub.id, session))
        return cases

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

test_service = TestService()
