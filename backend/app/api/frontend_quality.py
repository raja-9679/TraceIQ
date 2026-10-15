"""Frontend quality endpoints (WP10).

- run sweeps:       GET  /runs/{id}/sweep
- matrix presets:   GET  /frontend-matrix/presets
- site crawler:     /projects/{id}/site-crawls, /site-crawls/{id}[/run|/runs],
                    /site-crawl-runs/{id}[/issues]
- page budgets:     GET  /projects/{id}/page-budgets[/trend]
- selector health:  GET  /projects/{id}/selector-health
- Storybook:        POST /projects/{id}/storybook/stories, /storybook/cases

Every endpoint resolves the project first and checks access on it (editor for
writes); project-scoped API keys are held to their project.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field as PydField
from sqlalchemy import func
from sqlmodel import delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.jobs import api_key_allows_project
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import (
    PageBudgetSample, Project, ProjectEnvironment, SelectorHealProposal, SiteCrawl,
    SiteCrawlIssue, SiteCrawlRun, TestCase, TestCaseResult, TestRun, TestSuite,
)
from app.services import frontend_matrix, site_crawl, storybook
from app.services.access_service import access_service
from app.services.audit import record as audit_record

router = APIRouter()


async def _project_for(session: AsyncSession, principal: AuthPrincipal, project_id: Optional[int],
                       min_role: str = "viewer") -> Project:
    project = await session.get(Project, project_id) if project_id else None
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if principal.api_key is not None and not api_key_allows_project(principal.api_key, project_id):
        raise HTTPException(status_code=403, detail="API key is not scoped to this project")
    if not await access_service.has_project_access(principal.user.id, project_id, session,
                                                   min_role=min_role):
        raise HTTPException(status_code=403, detail="Access denied" if min_role == "viewer"
                            else f"{min_role.capitalize()} role required")
    return project


async def known_hosts(session: AsyncSession, project: Project):
    from app.services.access_matrix_runtime import security_settings_for
    envs = (await session.exec(select(ProjectEnvironment.base_url).where(
        ProjectEnvironment.project_id == project.id))).all()
    suites = (await session.exec(select(TestSuite.settings).where(
        TestSuite.project_id == project.id))).all()
    sec = security_settings_for(project).get("allowed_domains") or []
    return site_crawl.known_hosts_from(envs, suites, sec)


# ---------------------------------------------------------------------------
# Matrix presets + run sweeps
# ---------------------------------------------------------------------------

MAX_SWEEP_RUNS = 200
STORYBOOK_INDEX_MAX_BYTES = 20 * 1024 * 1024


@router.get("/frontend-matrix/presets")
async def matrix_presets(principal: AuthPrincipal = Depends(get_current_principal)):
    return {
        "viewports": [{"name": k, **v} for k, v in frontend_matrix.VIEWPORT_PRESETS.items()],
        "color_schemes": list(frontend_matrix.COLOR_SCHEMES),
        "max_fanout_runs": frontend_matrix.MAX_FANOUT_RUNS,
        "rtl_languages": sorted(frontend_matrix.RTL_LANGUAGES),
    }


@router.get("/runs/{run_id}/sweep")
async def run_sweep(run_id: int, session: AsyncSession = Depends(get_session),
                    principal: AuthPrincipal = Depends(get_current_principal)):
    """Every run of this run's sweep group with per-step screenshots, grouped
    for side-by-side display. Runs are filtered to the SAME project, so a
    guessed group id can never pull in another project's runs."""
    run = await session.get(TestRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    await _project_for(session, principal, run.project_id)
    if not run.sweep_group:
        return {"sweep_group": None, "variants": []}
    # A sweep over a whole suite is cases x variants runs; the side-by-side
    # view is bounded rather than loading every result payload of all of them.
    runs = (await session.exec(select(TestRun).where(
        TestRun.sweep_group == run.sweep_group,
        TestRun.project_id == run.project_id).order_by(TestRun.id).limit(MAX_SWEEP_RUNS + 1))).all()
    truncated = len(runs) > MAX_SWEEP_RUNS
    runs = runs[:MAX_SWEEP_RUNS]
    results = (await session.exec(select(TestCaseResult).where(
        TestCaseResult.test_run_id.in_([r.id for r in runs])))).all() if runs else []
    by_run: Dict[int, list] = {}
    for res in results:
        by_run.setdefault(res.test_run_id, []).append(res)
    variants = []
    for r in runs:
        emu = r.emulation or {}
        tests = []
        for res in sorted(by_run.get(r.id, []), key=lambda x: x.id):
            steps = []
            for e in ((res.result_payload or {}).get("step_evidence") or []):
                if isinstance(e, dict):
                    steps.append({k: e.get(k) for k in
                                  ("index", "step_id", "type", "status", "screenshot", "error")})
            tests.append({"result_id": res.id, "test_name": res.test_name,
                          "test_case_id": res.test_case_id,
                          "status": getattr(res.status, "value", res.status),
                          "error_message": res.error_message, "steps": steps})
        variants.append({
            "run_id": r.id, "test_case_id": r.test_case_id, "test_case_name": r.test_case_name,
            "browser": r.browser, "device": r.device,
            "status": getattr(r.status, "value", r.status),
            "label": emu.get("label") or r.device or r.browser,
            "emulation": emu, "rtl": frontend_matrix.is_rtl_locale((emu.get("locale") or {}).get("locale")),
            "tests": tests,
        })
    return {"sweep_group": run.sweep_group, "variants": variants, "truncated": truncated}


# ---------------------------------------------------------------------------
# Site crawler
# ---------------------------------------------------------------------------

class SiteCrawlIn(BaseModel):
    name: str = PydField(min_length=1, max_length=200)
    start_url: str = PydField(min_length=1, max_length=2000)
    max_depth: int = 3
    max_pages: int = 100
    extra_hosts: List[str] = []
    respect_robots: bool = True
    rate_limit_ms: int = 250
    slow_threshold_ms: int = 3000
    check_external: bool = True
    cron_expression: Optional[str] = None
    is_active: bool = True


class SiteCrawlPatch(BaseModel):
    name: Optional[str] = None
    start_url: Optional[str] = None
    max_depth: Optional[int] = None
    max_pages: Optional[int] = None
    extra_hosts: Optional[List[str]] = None
    respect_robots: Optional[bool] = None
    rate_limit_ms: Optional[int] = None
    slow_threshold_ms: Optional[int] = None
    check_external: Optional[bool] = None
    cron_expression: Optional[str] = None
    is_active: Optional[bool] = None


def _crawl_out(c: SiteCrawl, last_run: Optional[SiteCrawlRun] = None) -> Dict[str, Any]:
    out = c.model_dump()
    out["last_run"] = _run_out(last_run) if last_run else None
    return out


def _run_out(r: SiteCrawlRun, with_pages: bool = False) -> Dict[str, Any]:
    out = {k: getattr(r, k) for k in (
        "id", "crawl_id", "project_id", "status", "trigger", "created_at", "completed_at",
        "pages_crawled", "links_checked", "issue_counts", "truncated", "error", "robots",
        "requested_by_id")}
    out["start_url"] = (r.config or {}).get("start_url")
    if with_pages:
        out["pages"] = r.pages or []
        out["config"] = r.config
    return out


def _next_run(cron: Optional[str]) -> Optional[datetime]:
    if not cron:
        return None
    from croniter import croniter
    return croniter(cron, datetime.utcnow()).get_next(datetime)


async def _crawl_for(session, principal, crawl_id: int, min_role="viewer"):
    crawl = await session.get(SiteCrawl, crawl_id)
    if not crawl:
        raise HTTPException(status_code=404, detail="Crawl not found")
    project = await _project_for(session, principal, crawl.project_id, min_role)
    return crawl, project


@router.get("/projects/{project_id}/frontend-hosts")
async def project_hosts(project_id: int, session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    """Hosts the crawler / Storybook import may target for this project."""
    project = await _project_for(session, principal, project_id)
    return {"hosts": sorted(await known_hosts(session, project))}


@router.get("/projects/{project_id}/site-crawls")
async def list_crawls(project_id: int, session: AsyncSession = Depends(get_session),
                      principal: AuthPrincipal = Depends(get_current_principal)):
    await _project_for(session, principal, project_id)
    crawls = (await session.exec(select(SiteCrawl).where(
        SiteCrawl.project_id == project_id).order_by(SiteCrawl.id))).all()
    out = []
    for c in crawls:
        last = (await session.exec(select(SiteCrawlRun).where(SiteCrawlRun.crawl_id == c.id)
                                   .order_by(SiteCrawlRun.id.desc()).limit(1))).first()
        out.append(_crawl_out(c, last))
    return out


@router.post("/projects/{project_id}/site-crawls")
async def create_crawl(project_id: int, body: SiteCrawlIn, request: Request,
                       session: AsyncSession = Depends(get_session),
                       principal: AuthPrincipal = Depends(get_current_principal)):
    project = await _project_for(session, principal, project_id, "editor")
    try:
        data = site_crawl.validate_config(body.model_dump(), await known_hosts(session, project))
    except site_crawl.CrawlConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    existing = (await session.exec(select(func.count()).select_from(SiteCrawl).where(
        SiteCrawl.project_id == project_id))).one()
    if existing >= site_crawl.MAX_CRAWLS_PER_PROJECT:
        raise HTTPException(status_code=409, detail=(
            f"a project may hold at most {site_crawl.MAX_CRAWLS_PER_PROJECT} crawls; "
            "delete one or widen an existing crawl instead"))
    crawl = SiteCrawl(project_id=project_id, created_by_id=principal.user.id, **data)
    crawl.next_run_at = _next_run(crawl.cron_expression) if crawl.is_active else None
    session.add(crawl)
    await session.flush()
    await audit_record(session, entity_type="site_crawl", entity_id=crawl.id, action="create",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"name": crawl.name, "start_url": crawl.start_url,
                                "cron_expression": crawl.cron_expression})
    await session.commit()
    await session.refresh(crawl)
    return _crawl_out(crawl)


@router.patch("/site-crawls/{crawl_id}")
async def update_crawl(crawl_id: int, body: SiteCrawlPatch, request: Request,
                       session: AsyncSession = Depends(get_session),
                       principal: AuthPrincipal = Depends(get_current_principal)):
    crawl, project = await _crawl_for(session, principal, crawl_id, "editor")
    patch = body.model_dump(exclude_unset=True)
    try:
        data = site_crawl.validate_config(patch, await known_hosts(session, project))
    except site_crawl.CrawlConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    for k, v in data.items():
        setattr(crawl, k, v)
    crawl.updated_at = datetime.utcnow()
    crawl.next_run_at = _next_run(crawl.cron_expression) if crawl.is_active else None
    session.add(crawl)
    await audit_record(session, entity_type="site_crawl", entity_id=crawl.id, action="update",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes=data)
    await session.commit()
    await session.refresh(crawl)
    return _crawl_out(crawl)


@router.delete("/site-crawls/{crawl_id}")
async def delete_crawl(crawl_id: int, request: Request, session: AsyncSession = Depends(get_session),
                       principal: AuthPrincipal = Depends(get_current_principal)):
    crawl, project = await _crawl_for(session, principal, crawl_id, "editor")
    run_ids = select(SiteCrawlRun.id).where(SiteCrawlRun.crawl_id == crawl.id)
    await session.exec(delete(SiteCrawlIssue).where(SiteCrawlIssue.crawl_run_id.in_(run_ids)))
    await session.exec(delete(SiteCrawlRun).where(SiteCrawlRun.crawl_id == crawl.id))
    await audit_record(session, entity_type="site_crawl", entity_id=crawl.id, action="delete",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"name": crawl.name})
    await session.delete(crawl)
    await session.commit()
    return {"deleted": crawl_id}


@router.post("/site-crawls/{crawl_id}/run")
async def run_crawl(crawl_id: int, request: Request, session: AsyncSession = Depends(get_session),
                    principal: AuthPrincipal = Depends(get_current_principal)):
    crawl, project = await _crawl_for(session, principal, crawl_id, "editor")
    busy = (await session.exec(select(SiteCrawlRun.id).where(
        SiteCrawlRun.crawl_id == crawl.id,
        SiteCrawlRun.status.in_(("pending", "running"))))).first()
    if busy:
        raise HTTPException(status_code=409, detail=f"crawl run {busy} is still in progress")
    from app.services.site_crawl import MAX_ACTIVE_RUNS_PER_PROJECT, snapshot
    active = (await session.exec(select(func.count()).select_from(SiteCrawlRun).where(
        SiteCrawlRun.project_id == crawl.project_id,
        SiteCrawlRun.status.in_(("pending", "running"))))).one()
    if active >= MAX_ACTIVE_RUNS_PER_PROJECT:
        raise HTTPException(status_code=429, detail=(
            f"{active} crawl runs are already in progress for this project "
            f"(at most {MAX_ACTIVE_RUNS_PER_PROJECT}); try again when one finishes"))
    run = SiteCrawlRun(crawl_id=crawl.id, project_id=crawl.project_id, trigger="manual",
                       config=snapshot(crawl), requested_by_id=principal.user.id)
    session.add(run)
    await session.flush()
    await audit_record(session, entity_type="site_crawl", entity_id=crawl.id, action="run",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"crawl_run_id": run.id})
    await session.commit()
    await session.refresh(run)
    from app.tasks.frontend_quality_tasks import dispatch_site_crawl
    try:
        dispatch_site_crawl.delay(run.id)
    except Exception as exc:  # noqa: BLE001
        print(f"[SiteCrawl] could not queue crawl run {run.id}: {exc}")
    return _run_out(run)


@router.get("/site-crawls/{crawl_id}/runs")
async def list_crawl_runs(crawl_id: int, limit: int = Query(30, ge=1, le=200),
                          session: AsyncSession = Depends(get_session),
                          principal: AuthPrincipal = Depends(get_current_principal)):
    crawl, _ = await _crawl_for(session, principal, crawl_id)
    runs = (await session.exec(select(SiteCrawlRun).where(SiteCrawlRun.crawl_id == crawl.id)
                               .order_by(SiteCrawlRun.id.desc()).limit(limit))).all()
    return [_run_out(r) for r in runs]


async def _crawl_run_for(session, principal, run_id: int) -> SiteCrawlRun:
    run = await session.get(SiteCrawlRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Crawl run not found")
    await _project_for(session, principal, run.project_id)
    return run


@router.get("/site-crawl-runs/{run_id}")
async def get_crawl_run(run_id: int, session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    run = await _crawl_run_for(session, principal, run_id)
    return _run_out(run, with_pages=True)


@router.get("/site-crawl-runs/{run_id}/issues")
async def list_crawl_issues(
    run_id: int,
    category: Optional[List[str]] = Query(None),
    element: Optional[List[str]] = Query(None),
    page_url: Optional[str] = None,
    q: Optional[str] = Query(None, max_length=200),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    run = await _crawl_run_for(session, principal, run_id)
    stmt = select(SiteCrawlIssue).where(SiteCrawlIssue.crawl_run_id == run.id)
    if category:
        stmt = stmt.where(SiteCrawlIssue.category.in_(category))
    if element:
        stmt = stmt.where(SiteCrawlIssue.element.in_(element))
    if page_url:
        stmt = stmt.where(SiteCrawlIssue.page_url == page_url)
    if q:
        # Plain substring (ILIKE with the wildcards escaped), never a pattern.
        needle = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(SiteCrawlIssue.target_url.ilike(needle, escape="\\")
                          | SiteCrawlIssue.page_url.ilike(needle, escape="\\"))
    total = (await session.exec(select(func.count()).select_from(stmt.subquery()))).one()
    rows = (await session.exec(stmt.order_by(SiteCrawlIssue.id).offset(offset).limit(limit))).all()
    return {"total": total, "items": [r.model_dump() for r in rows]}


# ---------------------------------------------------------------------------
# Page budgets
# ---------------------------------------------------------------------------

@router.get("/projects/{project_id}/page-budgets")
async def page_budget_summary(project_id: int, days: int = Query(30, ge=1, le=365),
                              session: AsyncSession = Depends(get_session),
                              principal: AuthPrincipal = Depends(get_current_principal)):
    """Latest sample per (page, variant) with pass rate over the window."""
    await _project_for(session, principal, project_id)
    since = datetime.utcnow() - timedelta(days=days)
    rows = (await session.exec(select(PageBudgetSample).where(
        PageBudgetSample.project_id == project_id,
        PageBudgetSample.created_at >= since).order_by(PageBudgetSample.id.desc()).limit(5000))).all()
    pages: Dict[tuple, Dict[str, Any]] = {}
    for s in rows:
        key = (s.page_key, s.variant)
        entry = pages.get(key)
        if entry is None:
            entry = pages[key] = {
                "page_key": s.page_key, "variant": s.variant, "latest": s.model_dump(),
                "samples": 0, "failed": 0,
            }
        entry["samples"] += 1
        entry["failed"] += 0 if s.passed else 1
    return sorted(pages.values(), key=lambda e: (-e["failed"], e["page_key"]))


@router.get("/projects/{project_id}/page-budgets/trend")
async def page_budget_trend(project_id: int, page_key: str = Query(..., max_length=500),
                            variant: Optional[str] = None, limit: int = Query(50, ge=1, le=500),
                            session: AsyncSession = Depends(get_session),
                            principal: AuthPrincipal = Depends(get_current_principal)):
    await _project_for(session, principal, project_id)
    stmt = select(PageBudgetSample).where(PageBudgetSample.project_id == project_id,
                                          PageBudgetSample.page_key == page_key)
    stmt = stmt.where(PageBudgetSample.variant == variant) if variant else \
        stmt.where(PageBudgetSample.variant.is_(None))
    rows = (await session.exec(stmt.order_by(PageBudgetSample.id.desc()).limit(limit))).all()
    return [r.model_dump() for r in reversed(rows)]


# ---------------------------------------------------------------------------
# Selector health
# ---------------------------------------------------------------------------

@router.get("/projects/{project_id}/selector-health")
async def selector_health(project_id: int, days: int = Query(90, ge=1, le=365),
                          session: AsyncSession = Depends(get_session),
                          principal: AuthPrincipal = Depends(get_current_principal)):
    from app.services.selector_health import aggregate, failures_from_payload
    await _project_for(session, principal, project_id)
    since = datetime.utcnow() - timedelta(days=days)
    case_rows = (await session.exec(select(TestCase.id, TestCase.name, TestCase.test_suite_id)
                                    .where(TestCase.project_id == project_id))).all()
    cases = {cid: {"name": name, "suite_id": sid} for cid, name, sid in case_rows}
    heals = []
    if cases:
        for h in (await session.exec(select(SelectorHealProposal).where(
                SelectorHealProposal.test_case_id.in_(list(cases)),
                SelectorHealProposal.created_at >= since))).all():
            heals.append({"test_case_id": h.test_case_id, "step_id": h.step_id,
                          "old_selector": h.old_selector, "new_selector": h.new_selector,
                          "status": h.status, "confidence": h.confidence,
                          "created_at": h.created_at})
    rows = (await session.exec(
        select(TestCaseResult.test_case_id, TestCaseResult.result_payload, TestRun.id, TestRun.created_at)
        .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
        .where(TestRun.project_id == project_id, TestRun.created_at >= since,
               TestCaseResult.result_payload.is_not(None))
        .order_by(TestRun.id.desc()).limit(3000))).all()
    failures = []
    for case_id, payload, rid, created in rows:
        # A result whose case was deleted, or that names a case of another
        # project, is not this project's selector history.
        if case_id in cases:
            failures.extend(failures_from_payload(payload, case_id, rid, created))
    entries = aggregate(heals, failures, cases)
    return {"days": days, "entries": entries,
            "totals": {"selectors": len(entries),
                       "heals": sum(e["heal_count"] for e in entries),
                       "failures": sum(e["failure_count"] for e in entries)}}


# ---------------------------------------------------------------------------
# Storybook
# ---------------------------------------------------------------------------

class StorybookIndexIn(BaseModel):
    url: str = PydField(min_length=1, max_length=2000)


class StorybookCasesIn(BaseModel):
    url: str = PydField(min_length=1, max_length=2000)
    suite_id: int
    story_ids: List[str] = PydField(min_length=1, max_length=storybook.MAX_CASES_PER_REQUEST)
    visual: bool = True
    a11y: bool = True
    a11y_impact: str = "serious"


async def _load_stories(session, project, url: str):
    from app.core.net_guard import ResponseTooLarge, UnsafeUrlError, safe_get
    from urllib.parse import urlsplit
    try:
        root = storybook.base_url(url)
    except storybook.StorybookError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not site_crawl.host_allowed(urlsplit(root).hostname, await known_hosts(session, project)):
        raise HTTPException(status_code=400, detail=(
            f"{urlsplit(root).hostname} is not one of this project's hosts. Add it as an "
            "environment base URL, a suite allowed domain or a security allowlist entry first."))
    last_error = "no story index found"
    for index_url in storybook.index_urls(root):
        try:
            # Size-capped while streaming: the index is the Storybook host's
            # to choose, and checking len(content) afterwards would already
            # have buffered whatever it sent.
            resp = await safe_get(index_url, timeout=20.0, max_bytes=STORYBOOK_INDEX_MAX_BYTES)
        except ResponseTooLarge:
            raise HTTPException(status_code=400, detail="story index is larger than 20 MB")
        except UnsafeUrlError as exc:
            raise HTTPException(status_code=400, detail=f"Storybook URL refused: {exc}")
        except Exception as exc:  # noqa: BLE001 — network errors are the user's to fix
            raise HTTPException(status_code=502, detail=f"could not reach Storybook: {type(exc).__name__}")
        if resp.status_code == 404:
            continue
        if resp.status_code >= 300:
            last_error = f"{index_url} answered {resp.status_code}"
            continue
        if len(resp.content) > 20 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="story index is larger than 20 MB")
        try:
            return root, storybook.parse_index(resp.json())
        except (ValueError, storybook.StorybookError) as exc:
            last_error = str(exc)
    raise HTTPException(status_code=400, detail=f"could not read the story index: {last_error}")


@router.post("/projects/{project_id}/storybook/stories")
async def storybook_stories(project_id: int, body: StorybookIndexIn,
                            session: AsyncSession = Depends(get_session),
                            principal: AuthPrincipal = Depends(get_current_principal)):
    project = await _project_for(session, principal, project_id)
    root, stories = await _load_stories(session, project, body.url)
    return {"root": root, "stories": stories}


@router.post("/projects/{project_id}/storybook/cases")
async def storybook_cases(project_id: int, body: StorybookCasesIn, request: Request,
                          session: AsyncSession = Depends(get_session),
                          principal: AuthPrincipal = Depends(get_current_principal)):
    """One case per picked story (visual + a11y). A story that already has a
    generated case in the suite is updated in place, keeping its baselines."""
    project = await _project_for(session, principal, project_id, "editor")
    suite = await session.get(TestSuite, body.suite_id)
    if not suite or suite.project_id != project.id:
        raise HTTPException(status_code=400, detail="suite_id does not belong to this project")
    root, stories = await _load_stories(session, project, body.url)
    try:
        picked = storybook.pick(stories, body.story_ids)
    except storybook.StorybookError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    existing = (await session.exec(select(TestCase).where(TestCase.test_suite_id == suite.id))).all()
    created, updated = [], []
    for story in picked:
        try:
            steps = storybook.build_steps(root, story["id"], visual=body.visual, a11y=body.a11y,
                                          a11y_impact=body.a11y_impact)
        except storybook.StorybookError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        case = storybook.find_existing(existing, story["id"])
        if case:
            case.steps = steps
            case.updated_at = datetime.utcnow()
            case.updated_by_id = principal.user.id
            session.add(case)
            updated.append(case)
        else:
            case = TestCase(name=storybook.case_name(story), steps=steps, test_suite_id=suite.id,
                            project_id=project.id, created_by_id=principal.user.id,
                            tags=["storybook", "component"])
            session.add(case)
            existing.append(case)
            created.append(case)
    await session.flush()
    await audit_record(session, entity_type="suite", entity_id=suite.id, action="storybook_import",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"storybook": root, "created": [c.id for c in created],
                                "updated": [c.id for c in updated]})
    await session.commit()
    return {"created": [{"id": c.id, "name": c.name} for c in created],
            "updated": [{"id": c.id, "name": c.name} for c in updated]}
