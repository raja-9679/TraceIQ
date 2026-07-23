"""External test-result ingestion — JUnit XML from the team's own CI.

TraceIQ executes UI/API/security tests itself; unit and integration suites
stay in the team's CI. This endpoint lets that CI push its JUnit report per
commit so the quality dashboard and the release gate cover *all* testing,
including the tests TraceIQ never runs.

    curl -X POST .../api/projects/1/external-results?git_commit=abc123 \
         -H "X-API-Key: tiq_..." -H "Content-Type: application/xml" \
         --data-binary @junit.xml
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import ExternalTestReport, ExternalTestReportRead, Project
from app.services.access_service import access_service

router = APIRouter()

_MAX_BODY_BYTES = 5 * 1024 * 1024
_MAX_FAILED_CASES = 50
_MAX_MESSAGE_CHARS = 500


def parse_junit_xml(raw: bytes) -> dict:
    """Parse a JUnit XML report (a `<testsuites>` wrapper or a bare
    `<testsuite>`). Returns totals plus a truncated failed-case list.
    Raises ValueError on malformed input."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f"Not valid XML: {exc}") from exc

    if root.tag == "testsuites":
        suites = list(root.iter("testsuite"))
    elif root.tag == "testsuite":
        suites = [root]
    else:
        raise ValueError(f"Expected <testsuites> or <testsuite> root, got <{root.tag}>")

    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time_seconds": 0.0}
    failed_cases: List[dict] = []
    suite_names: List[str] = []

    for suite in suites:
        if suite.get("name"):
            suite_names.append(suite.get("name"))
        # Prefer counting testcases directly — suite-level attributes lie in
        # some producers, and nested <testsuite> wrappers double-count.
        cases = suite.findall("testcase")
        totals["tests"] += len(cases)
        try:
            totals["time_seconds"] += float(suite.get("time") or 0)
        except ValueError:
            pass
        for case in cases:
            failure = case.find("failure")
            error = case.find("error")
            if case.find("skipped") is not None:
                totals["skipped"] += 1
            elif failure is not None or error is not None:
                node = failure if failure is not None else error
                totals["failures" if failure is not None else "errors"] += 1
                if len(failed_cases) < _MAX_FAILED_CASES:
                    failed_cases.append({
                        "name": case.get("name") or "?",
                        "classname": case.get("classname") or "",
                        "message": ((node.get("message") or (node.text or "").strip())[:_MAX_MESSAGE_CHARS]),
                    })

    return {
        **totals,
        "failed_cases": failed_cases or None,
        "suite_name": ", ".join(suite_names[:5]) or None,
    }


@router.post("/projects/{project_id}/external-results", response_model=ExternalTestReportRead)
async def ingest_external_results(
    project_id: int,
    request: Request,
    git_commit: Optional[str] = Query(None),
    git_branch: Optional[str] = Query(None),
    suite: Optional[str] = Query(None, description="Override the suite name"),
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    """Ingest one JUnit XML report (request body = the raw XML)."""
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not await access_service.has_project_access(
            principal.user.id, project_id, session, min_role="editor"):
        raise HTTPException(status_code=403, detail="Access denied")

    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty body — send the JUnit XML as the request body")
    if len(raw) > _MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Report too large (max 5 MB)")

    try:
        parsed = parse_junit_xml(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    report = ExternalTestReport(
        project_id=project_id,
        source="junit",
        suite_name=suite or parsed["suite_name"],
        git_commit=git_commit,
        git_branch=git_branch,
        tests=parsed["tests"],
        failures=parsed["failures"],
        errors=parsed["errors"],
        skipped=parsed["skipped"],
        time_seconds=round(parsed["time_seconds"], 3),
        failed_cases=parsed["failed_cases"],
        uploaded_by=(f"api-key:{principal.api_key.name}" if getattr(principal, "api_key", None)
                     else principal.user.email),
    )
    session.add(report)
    await session.commit()
    await session.refresh(report)
    return report


@router.get("/projects/{project_id}/external-results", response_model=List[ExternalTestReportRead])
async def list_external_results(
    project_id: int,
    git_commit: Optional[str] = Query(None),
    limit: int = Query(20, le=100),
    session: AsyncSession = Depends(get_session),
    principal: AuthPrincipal = Depends(get_current_principal),
):
    if not await access_service.has_project_access(principal.user.id, project_id, session):
        raise HTTPException(status_code=403, detail="Access denied")
    query = select(ExternalTestReport).where(ExternalTestReport.project_id == project_id)
    if git_commit:
        query = query.where(ExternalTestReport.git_commit == git_commit)
    query = query.order_by(ExternalTestReport.created_at.desc()).limit(limit)
    return (await session.exec(query)).all()
