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
        "type": "check",
        "category": "interaction",
        "params": {"selector": "Checkbox / radio to check"},
        "example": {"id": "<uuid>", "type": "check", "selector": "#accept-terms"},
    },
    {
        "type": "uncheck",
        "category": "interaction",
        "params": {"selector": "Checkbox to uncheck"},
        "example": {"id": "<uuid>", "type": "uncheck", "selector": "#newsletter-opt-in"},
    },
    {
        "type": "double-click",
        "category": "interaction",
        "params": {"selector": "Element to double-click"},
        "example": {"id": "<uuid>", "type": "double-click", "selector": ".file-row[data-name='report.pdf']"},
    },
    {
        "type": "right-click",
        "category": "interaction",
        "params": {"selector": "Element to right-click (opens context menus)"},
        "example": {"id": "<uuid>", "type": "right-click", "selector": ".tree-node"},
    },
    {
        "type": "drag-and-drop",
        "category": "interaction",
        "params": {"selector": "Source element", "value": "Target element selector"},
        "example": {"id": "<uuid>", "type": "drag-and-drop", "selector": ".card[data-id='7']", "value": ".column[data-status='done']"},
    },
    {
        "type": "upload-file",
        "category": "interaction",
        "params": {
            "selector": "The <input type=file> element",
            "params.files": "Inline fixtures: [{name, content_base64}] written to a temp dir on the worker",
            "value": "Alternative: comma-separated worker-local file paths (pre-mounted fixtures)",
        },
        "example": {
            "id": "<uuid>",
            "type": "upload-file",
            "selector": "input[type=file]",
            "params": {"files": [{"name": "avatar.png", "content_base64": "<base64>"}]},
        },
        "notes": "Prefer params.files — inline base64 fixtures travel with the test case and need nothing on the worker.",
    },
    {
        "type": "download-file",
        "category": "interaction",
        "params": {
            "params.trigger_selector": "Element clicked to start the download (required in practice — steps are sequential, so the trigger must overlap the wait)",
            "params.filename_contains": "Assert the suggested filename contains this substring (optional)",
            "params.variableName": "Store the downloaded filename in this variable (optional)",
        },
        "example": {
            "id": "<uuid>",
            "type": "download-file",
            "params": {"trigger_selector": "#export-csv", "filename_contains": ".csv"},
        },
        "notes": "The file is saved with the run's artifacts.",
    },
    {
        "type": "handle-dialog",
        "category": "interaction",
        "params": {
            "params.action": "'accept' (default) or 'dismiss'",
            "params.prompt_text": "Text typed into prompt() dialogs before accepting (optional)",
            "params.variableName": "Store the dialog message for a later assert (optional)",
        },
        "example": {
            "id": "<uuid>",
            "type": "handle-dialog",
            "params": {"action": "accept"},
        },
        "notes": "Arms a one-shot handler for the NEXT dialog — place this step BEFORE the click that triggers the alert/confirm/prompt. Without it, Playwright auto-dismisses dialogs.",
    },
    {
        "type": "switch-tab",
        "category": "navigation",
        "params": {
            "value": "'latest' | 1-based tab index | URL substring",
            "params.trigger_selector": "Element clicked to open the popup/new tab; the step waits for the new page (optional)",
        },
        "example": {
            "id": "<uuid>",
            "type": "switch-tab",
            "value": "latest",
            "params": {"trigger_selector": "a[target=_blank]"},
        },
        "notes": "Subsequent steps run against the switched-to tab. Use `switch-tab` with value '1' to return to the first tab.",
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
        "type": "wait-for-response",
        "category": "wait",
        "params": {
            "value": "URL substring the response must contain",
            "params.status": "Expected HTTP status of the matching response (optional)",
            "params.trigger_selector": "Element clicked AFTER arming the wait, so the request it fires is caught (optional)",
            "params.variableName": "Store the matched response status (optional)",
        },
        "example": {
            "id": "<uuid>",
            "type": "wait-for-response",
            "value": "/api/search",
            "params": {"status": 200, "trigger_selector": "#search-btn"},
        },
        "notes": "Prefer this over wait-timeout for XHR-driven UIs. Use trigger_selector when a click fires the request — sequential steps cannot overlap otherwise.",
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
        "type": "expect-not-text",
        "category": "assertion",
        "params": {"selector": "Element to check (must exist)", "value": "Text that must be ABSENT from the element"},
        "example": {"id": "<uuid>", "type": "expect-not-text", "selector": ".todo-list", "value": "Deleted item"},
        "notes": "The element itself must exist; to assert an element is gone entirely use expect-hidden.",
    },
    {
        "type": "expect-url",
        "category": "assertion",
        "params": {"value": "URL pattern (use Playwright glob: '**/dashboard**', not '/dashboard')"},
        "notes": "Playwright's waitForURL with a bare string does EXACT match. Use globs (`**`) for substring.",
    },
    {
        "type": "expect-title",
        "category": "assertion",
        "params": {
            "value": "Expected page title (or fragment)",
            "params.operator": "'contains' (default) | 'equals' | 'matches' (regex)",
        },
        "example": {"id": "<uuid>", "type": "expect-title", "value": "Dashboard"},
    },
    {
        "type": "assert",
        "category": "assertion",
        "params": {
            "selector": "Element to inspect",
            "params.source": "'text' | 'value' | 'attribute' | 'count' | 'css'",
            "params.operator": "'equals' | 'contains' | ...",
            "params.attribute": "Required when source='attribute'",
            "params.property": "CSS property name, required when source='css' (compared against the computed style, e.g. 'color' -> 'rgb(255, 0, 0)')",
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
        "type": "mock-response",
        "category": "network",
        "params": {
            "selector": "URL glob to intercept (e.g. '**/api/user')",
            "params.status": "HTTP status to return (default 200)",
            "params.json": "JSON body to return (sets content-type application/json)",
            "params.body": "Raw string body (when not JSON)",
            "params.content_type": "Override content-type",
            "params.headers": "Response headers object",
        },
        "example": {"id": "<uuid>", "type": "mock-response", "selector": "**/api/me",
                    "params": {"status": 200, "json": {"name": "Test User"}}},
        "notes": "Register BEFORE the goto/action that triggers the request.",
    },
    {
        "type": "block-request",
        "category": "network",
        "params": {"selector": "URL glob to abort (e.g. '**/analytics/**')"},
        "example": {"id": "<uuid>", "type": "block-request", "selector": "**/gtag/**"},
    },
    {
        "type": "set-network-latency",
        "category": "network",
        "params": {"selector": "URL glob (default '**/*')", "params.ms": "Delay in ms before continuing"},
        "example": {"id": "<uuid>", "type": "set-network-latency", "selector": "**/api/**", "params": {"ms": 2000}},
    },
    {
        "type": "check-accessibility",
        "category": "assertion",
        "params": {
            "params.impact": "Minimum severity that fails the step: 'minor'|'moderate'|'serious'(default)|'critical'",
            "params.fail": "false = report-only (never throws)",
        },
        "example": {"id": "<uuid>", "type": "check-accessibility", "params": {"impact": "serious"}},
        "notes": "Runs axe-core against the current page; returns a violations summary.",
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
            "params.extract": "Array of {path, variable, required?} — store JSON response values as runtime vars",
        },
        "notes": (
            "Assertion shapes inside params.assertions: "
            "{type:'status', value:200}; "
            "{type:'json-path', path:'postgres.articles', operator:'equals'|'contains', value:...}; "
            "{type:'json-schema', value:'<JSON-schema-as-string>'} — json-path uses dotted paths (NO '$.' prefix); "
            "array indexing via 'items.0.id' or 'items[0].id'. "
            "params.extract chains requests: extract {path:'data.token', variable:'token'} then use "
            "{{token}} in a later step's headers/body/url. Extraction failure fails the step unless required:false."
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
        "type": "graphql",
        "category": "api",
        "params": {
            "value": "GraphQL endpoint URL",
            "params.query": "GraphQL query/mutation string",
            "params.variables": "Optional variables object",
            "params.headers": "Optional extra headers (merged over suite headers)",
            "params.allow_errors": "true to skip the default errors[]-must-be-empty check",
            "params.assertions": "Array of {type:'status', value} or {type:'data-path', path, operator:'equals'|'contains'|'exists', value}",
            "params.extract": "Array of {path, variable} — paths relative to response `data`",
        },
        "example": {
            "id": "<uuid>",
            "type": "graphql",
            "value": "http://app/graphql",
            "params": {
                "query": "query($id: ID!) { user(id: $id) { name email } }",
                "variables": {"id": "1"},
                "assertions": [{"type": "data-path", "path": "user.name", "operator": "exists"}],
            },
        },
        "notes": "POSTs {query, variables}. Fails when the response carries GraphQL errors[] unless allow_errors. data-path paths omit the leading 'data.'.",
    },
    {
        "type": "oauth2-token",
        "category": "api",
        "params": {
            "value": "Token endpoint URL",
            "params.client_id": "Client id (supports {{secret.X}})",
            "params.client_secret": "Client secret — ALWAYS use {{secret.X}}",
            "params.scope": "Optional scope",
            "params.audience": "Optional audience",
            "params.variable": "Runtime var for the token (default 'access_token')",
        },
        "example": {
            "id": "<uuid>",
            "type": "oauth2-token",
            "value": "http://app/oauth/token",
            "params": {"client_id": "{{secret.CLIENT_ID}}", "client_secret": "{{secret.CLIENT_SECRET}}"},
        },
        "notes": "client_credentials grant for the app under test. Later steps use {{access_token}} e.g. headers: {'Authorization': 'Bearer {{access_token}}'}.",
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
