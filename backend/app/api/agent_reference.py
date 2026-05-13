"""Phase E — agent reference + bulk-ops endpoints.

Three flavors of endpoint:
  1. Reference: `/api/step-types`, `/api/agent-guide` — what TraceIQ supports.
     Hand-maintained content the agent reads once to ground its authoring.
  2. Bulk ops: `/api/cases/bulk-propose`, `/api/cases/bulk-set-code-paths` —
     authoring at fleet-scale without a round-trip per case.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import (
    CaseProposal,
    CaseProposalAction,
    CaseProposalCreate,
    CaseProposalRead,
    Project,
    TestCase,
)
from app.services.access_service import access_service

router = APIRouter()


# ---------------------------------------------------------------------------
# Reference
# ---------------------------------------------------------------------------

# Hand-maintained step-type catalog. Mirrors what
# `execution-engine/src/core/test-executor.ts` actually implements. The agent
# uses this to know what step types are valid and what params each accepts —
# without having to read the runner source.
#
# Keep this in sync when adding a new step type to test-executor.ts. A CI
# script can later parse the switch statement and warn when entries diverge.
_STEP_TYPES: List[Dict[str, Any]] = [
    {
        "type": "goto",
        "category": "navigation",
        "params": {"value": "Absolute URL to navigate to"},
        "example": {"id": "<uuid>", "type": "goto", "value": "http://app/login"},
        "notes": "Use full URLs reachable from the worker container (often via the docker bridge gateway).",
    },
    {
        "type": "click",
        "category": "interaction",
        "params": {"selector": "CSS / Playwright selector"},
        "example": {"id": "<uuid>", "type": "click", "selector": "#submit-button"},
    },
    {
        "type": "fill",
        "category": "interaction",
        "params": {"selector": "Selector of an <input>/<textarea>/[contenteditable]", "value": "Text to type"},
        "example": {"id": "<uuid>", "type": "fill", "selector": "#email", "value": "alice@example.com"},
        "notes": "Does NOT work on <select> — use `select-option` instead.",
    },
    {
        "type": "select-option",
        "category": "interaction",
        "params": {"selector": "Selector of a <select>", "value": "Option value to choose"},
        "example": {"id": "<uuid>", "type": "select-option", "selector": "#priority", "value": "high"},
    },
    {
        "type": "press-key",
        "category": "interaction",
        "params": {"value": "Key name, e.g. 'Enter', 'Escape', 'ArrowDown'"},
        "example": {"id": "<uuid>", "type": "press-key", "value": "Enter"},
    },
    {
        "type": "hover",
        "category": "interaction",
        "params": {"selector": "Element to hover"},
        "example": {"id": "<uuid>", "type": "hover", "selector": "[data-testid='profile-menu']"},
    },
    {
        "type": "scroll-to",
        "category": "interaction",
        "params": {"selector": "Element to scroll into view"},
    },
    {
        "type": "wait-for-selector",
        "category": "wait",
        "params": {"selector": "Selector to wait for"},
        "notes": "BEWARE: this blocks on in-flight navigations. If a redirect is happening, the wait can stall. Use `wait-timeout` + a title/URL assertion instead when the page may navigate.",
    },
    {
        "type": "wait-timeout",
        "category": "wait",
        "params": {"value": "Milliseconds to wait (string)"},
        "example": {"id": "<uuid>", "type": "wait-timeout", "value": "1500"},
    },
    {
        "type": "expect-visible",
        "category": "assertion",
        "params": {"selector": "Element that must be visible"},
    },
    {
        "type": "expect-hidden",
        "category": "assertion",
        "params": {"selector": "Element that must NOT be visible"},
    },
    {
        "type": "expect-text",
        "category": "assertion",
        "params": {"selector": "Element to check", "value": "Expected text (default: exact match)"},
    },
    {
        "type": "expect-url",
        "category": "assertion",
        "params": {"value": "URL pattern (use Playwright glob: '**/dashboard**', not '/dashboard')"},
        "notes": "Playwright's waitForURL with a bare string does EXACT match. Use globs (`**`) for substring.",
    },
    {
        "type": "assert",
        "category": "assertion",
        "params": {
            "selector": "Element to inspect",
            "params.source": "'text' | 'value' | 'attribute' | 'count'",
            "params.operator": "'equals' | 'contains' | ...",
            "params.attribute": "Required when source='attribute'",
            "value": "Expected value",
        },
        "example": {
            "id": "<uuid>",
            "type": "assert",
            "selector": "title",
            "params": {"source": "text", "operator": "contains"},
            "value": "Dashboard",
        },
    },
    {
        "type": "screenshot",
        "category": "capture",
        "params": {"value": "Filename (no extension)"},
    },
    {
        "type": "expect-visual-match",
        "category": "assertion",
        "params": {},
        "notes": "Phase B. Compares against a stored VisualBaseline keyed by (test_case_id, step.id, browser, device).",
    },
    {
        "type": "http-request",
        "category": "api",
        "params": {
            "value": "URL to call (comma-separated for batch)",
            "params.method": "'GET'|'POST'|'PUT'|'PATCH'|'DELETE' (default 'GET')",
            "params.headers": "Object — e.g. {'Authorization': 'Bearer ...'}",
            "params.body": "Request body (object or string)",
            "params.params": "Query-param object",
            "params.assertions": "Array of {type, ...} — see assertion shapes below",
        },
        "notes": (
            "Assertion shapes inside params.assertions: "
            "{type:'status', value:200}; "
            "{type:'json-path', path:'postgres.articles', operator:'equals'|'contains', value:...}; "
            "{type:'json-schema', value:'<JSON-schema-as-string>'} — json-path uses dotted paths (NO '$.' prefix)."
        ),
        "example": {
            "id": "<uuid>",
            "type": "http-request",
            "value": "http://app/api/overview",
            "params": {
                "method": "GET",
                "headers": {"Authorization": "Bearer ..."},
                "assertions": [
                    {"type": "status", "value": 200},
                    {"type": "json-path", "path": "status", "operator": "equals", "value": "healthy"},
                ],
            },
        },
    },
    {
        "type": "feed-check",
        "category": "api",
        "params": {"value": "URL to fetch", "params.assertions": "Same shape as http-request assertions"},
        "notes": "Self-contained fetch+assert. NOT a way to assert on a previous step's response — use http-request's embedded assertions for that.",
    },
    {
        "type": "run-script",
        "category": "interaction",
        "params": {
            "params.language": "'javascript' (default) | 'python'",
            "params.body": "Script body — receives `variables` (Object) — return value stored under params.variableName",
            "params.variableName": "Optional. Where to save the return value in the test context.",
        },
        "example": {
            "id": "<uuid>",
            "type": "run-script",
            "params": {
                "language": "javascript",
                "body": "localStorage.setItem('user', JSON.stringify({token: 'X'})); return 'ok';",
            },
        },
    },
    {
        "type": "extract-value",
        "category": "data",
        "params": {"selector": "Element", "params.source": "'text'|'value'|'attribute'", "params.variableName": "Variable name to save under"},
    },
    {
        "type": "switch-frame",
        "category": "navigation",
        "params": {"selector": "iframe selector"},
    },
    {
        "type": "carousel-find",
        "category": "interaction",
        "params": {"selector": "Target", "value": "Next-button selector", "params.max_swipes": "Default 10"},
    },
    {
        "type": "verify-nth-child",
        "category": "assertion",
        "params": {"selector": "Parent", "params.n": "Index", "value": "Expected text/attr"},
    },
    {
        "type": "count-children",
        "category": "assertion",
        "params": {"selector": "Parent", "params.operator": "'equals' | 'gt' | 'gte' | 'lt' | 'lte'", "value": "Expected count"},
    },
    {
        "type": "amp-validate",
        "category": "api",
        "params": {"value": "URL of an AMP doc"},
        "notes": "Validates AMP HTML compliance.",
    },
]


@router.get("/step-types")
async def list_step_types() -> Dict[str, Any]:
    """Catalogue of every step type the runner supports plus its params shape.

    No auth — this is reference content an agent can pull early in a session
    to ground its proposals. Includes examples and known gotchas.
    """
    return {
        "step_types": _STEP_TYPES,
        "total": len(_STEP_TYPES),
        "last_updated": "2026-05-13",
    }


@router.get("/agent-guide")
async def get_agent_guide() -> Dict[str, Any]:
    """Returns the bundled AGENT_GUIDE.md as text + last-modified timestamp.

    The MCP server's `get_authoring_guide` tool surfaces this to the agent
    at the start of a session. The guide explains conventions
    (suite layout, code_paths globs, persona auth) and the five common
    pitfalls — designed so a fresh agent can author correct cases without
    relearning the same lessons.

    Path resolution:
      1. $TRACEIQ_AGENT_GUIDE_PATH — operator override
      2. <backend>/app/AGENT_GUIDE.md — bundled with the image
      3. <repo>/integrations/mcp-server/AGENT_GUIDE.md — dev/host run
    """
    import os as _os
    candidates: List[Optional[Path]] = [
        Path(_os.environ["TRACEIQ_AGENT_GUIDE_PATH"]) if _os.environ.get("TRACEIQ_AGENT_GUIDE_PATH") else None,
        # bundled copy (Dockerfile COPY . . picks it up from backend/app/)
        Path(__file__).resolve().parent.parent / "AGENT_GUIDE.md",
        # dev / host path (canonical source)
        Path(__file__).resolve().parents[3] / "integrations" / "mcp-server" / "AGENT_GUIDE.md",
    ]
    guide_path: Optional[Path] = None
    for c in candidates:
        if c and c.exists():
            guide_path = c
            break
    if not guide_path:
        raise HTTPException(
            status_code=503,
            detail=f"AGENT_GUIDE.md not found in any of: {[str(c) for c in candidates if c]}",
        )
    body = guide_path.read_text(encoding="utf-8")
    try:
        mtime = guide_path.stat().st_mtime
        last_modified = datetime.utcfromtimestamp(mtime).isoformat() + "Z"
    except OSError:
        last_modified = None
    return {
        "guide": body,
        "last_modified": last_modified,
        "size_chars": len(body),
        "source_path": str(guide_path),
    }


# ---------------------------------------------------------------------------
# Bulk-propose
# ---------------------------------------------------------------------------

class BulkProposalRequest(BaseModel):
    project_id: int
    proposals: List[CaseProposalCreate]


class BulkProposalItemResult(BaseModel):
    index: int
    status: str  # "created" | "rejected"
    proposal_id: Optional[int] = None
    error: Optional[str] = None


class BulkProposalResponse(BaseModel):
    project_id: int
    submitted: int
    created: int
    rejected: int
    results: List[BulkProposalItemResult]


@router.post("/cases/bulk-propose", response_model=BulkProposalResponse)
async def bulk_propose_cases(
    body: BulkProposalRequest,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> BulkProposalResponse:
    """Submit many CaseProposals in one round-trip.

    Best-effort: a single bad proposal doesn't roll back the rest. Each
    item gets a per-index result so the agent knows what to retry.
    """
    if not await access_service.has_project_access(
        principal.user.id, body.project_id, session, min_role="editor"
    ):
        raise HTTPException(status_code=403, detail="Editor access required")

    results: List[BulkProposalItemResult] = []
    created = 0
    for idx, p in enumerate(body.proposals):
        try:
            if p.project_id != body.project_id:
                raise ValueError(f"proposal[{idx}].project_id mismatch with outer body")
            if p.action == CaseProposalAction.CREATE and p.test_suite_id is None:
                raise ValueError("CREATE requires test_suite_id")
            if p.action in (CaseProposalAction.UPDATE, CaseProposalAction.DELETE, CaseProposalAction.MOVE):
                if p.target_case_id is None:
                    raise ValueError(f"{p.action.value.upper()} requires target_case_id")
            proposal = CaseProposal(
                project_id=p.project_id,
                test_suite_id=p.test_suite_id,
                target_case_id=p.target_case_id,
                action=p.action,
                payload=p.payload or {},
                rationale=p.rationale,
                ai_confidence=p.ai_confidence,
                agent_id=principal.agent_id,
                created_by_agent_id=principal.agent_id,
                agent_session_id=principal.agent_session_id,
            )
            session.add(proposal)
            await session.flush()
            results.append(BulkProposalItemResult(index=idx, status="created", proposal_id=proposal.id))
            created += 1
        except Exception as exc:  # noqa: BLE001
            results.append(BulkProposalItemResult(index=idx, status="rejected", error=str(exc)[:200]))

    await session.commit()
    return BulkProposalResponse(
        project_id=body.project_id,
        submitted=len(body.proposals),
        created=created,
        rejected=len(body.proposals) - created,
        results=results,
    )


# ---------------------------------------------------------------------------
# Bulk-set-code-paths
# ---------------------------------------------------------------------------

class BulkCodePathsRequest(BaseModel):
    project_id: int
    mapping: Dict[int, List[str]]  # case_id → list of code path globs/prefixes


class BulkCodePathsItemResult(BaseModel):
    case_id: int
    status: str  # "updated" | "skipped" | "error"
    error: Optional[str] = None


class BulkCodePathsResponse(BaseModel):
    project_id: int
    submitted: int
    updated: int
    results: List[BulkCodePathsItemResult]


@router.post("/cases/bulk-set-code-paths", response_model=BulkCodePathsResponse)
async def bulk_set_code_paths(
    body: BulkCodePathsRequest,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> BulkCodePathsResponse:
    """Atomically (per-row) set `code_paths` on many cases.

    Used by Mode-1 agents that walk source code locally and want to push
    file→case mapping into TraceIQ in one shot. Cases in projects the
    caller can't access are silently skipped (no info leak).
    """
    if not await access_service.has_project_access(
        principal.user.id, body.project_id, session, min_role="editor"
    ):
        raise HTTPException(status_code=403, detail="Editor access required")

    results: List[BulkCodePathsItemResult] = []
    updated = 0
    for case_id, paths in body.mapping.items():
        try:
            case = await session.get(TestCase, int(case_id))
            if not case:
                results.append(BulkCodePathsItemResult(case_id=case_id, status="skipped", error="not found"))
                continue
            if case.project_id != body.project_id:
                results.append(BulkCodePathsItemResult(case_id=case_id, status="skipped",
                                                       error="not in requested project"))
                continue
            case.code_paths = paths
            session.add(case)
            results.append(BulkCodePathsItemResult(case_id=case_id, status="updated"))
            updated += 1
        except Exception as exc:  # noqa: BLE001
            results.append(BulkCodePathsItemResult(case_id=case_id, status="error", error=str(exc)[:200]))

    await session.commit()
    return BulkCodePathsResponse(
        project_id=body.project_id,
        submitted=len(body.mapping),
        updated=updated,
        results=results,
    )
