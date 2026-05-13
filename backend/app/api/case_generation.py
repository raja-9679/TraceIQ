"""Phase C — AI-driven and schema-driven test case generation.

Two endpoints:

    POST /api/cases/generate
        Body: { description, target_url?, test_suite_id, case_name? }
        → LLM emits a TestCase draft (steps with intents).

    POST /api/cases/from-openapi
        Body: { schema_url? | schema_inline?, test_suite_id, base_url?, operations? }
        → Parses the OpenAPI doc, emits one TestCase per operation with
          `http-request` + `feed-check` steps.

Both endpoints create the case in the requested suite and return the
serialized result. The generated case is fully editable afterwards — the
generator's output is treated as a draft, not a contract.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.ai.providers import provider as llm_provider
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import (
    CaseFromOpenAPIRequest,
    CaseGenerationRequest,
    CaseProposal,
    CaseProposalAction,
    Project,
    TestCase,
    TestCaseRead,
    TestSuite,
    Workspace,
)
from app.services.access_service import access_service

router = APIRouter()


# ---------------------------------------------------------------------------
# Phase D: AI-generation budget cap
# ---------------------------------------------------------------------------
async def _check_and_consume_ai_budget(workspace_id: int, session: AsyncSession) -> None:
    """Enforce a per-workspace daily cap on AI-generation calls.

    Uses a Redis counter `workspace:{id}:ai_gen:{YYYY-MM-DD}`. Fails open
    (allows the call) if Redis is unreachable — generation is best-effort
    and the cap is a guardrail against runaway spend, not a hard SLO.
    """
    workspace = await session.get(Workspace, workspace_id)
    if not workspace:
        return
    limit = workspace.ai_generation_limit_daily or 0
    if limit <= 0:
        return  # unlimited
    try:
        import redis.asyncio as redis
        from app.core.config import settings as _settings
        r = redis.from_url(_settings.CELERY_BROKER_URL, decode_responses=True)
        key = f"workspace:{workspace_id}:ai_gen:{datetime.utcnow():%Y-%m-%d}"
        used = await r.incr(key)
        if used == 1:
            await r.expire(key, 60 * 60 * 36)  # 36h TTL covers day boundary
        await r.close()
        if used > limit:
            raise HTTPException(
                status_code=429,
                detail=f"Workspace daily AI generation cap reached ({limit}). "
                       "Raise `ai_generation_limit_daily` on the workspace or wait until UTC midnight.",
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[CaseGeneration] budget check failed open: {exc}")


# ---------------------------------------------------------------------------
# Test-from-intent
# ---------------------------------------------------------------------------

_GENERATION_SYSTEM = (
    "You generate UI test cases for TraceIQ. Output STRICT JSON: an array "
    "of step objects. Each step has fields: {id (uuid), type, selector, "
    "value, intent}. Supported types: goto, click, fill, expect-visible, "
    "expect-text, expect-url, hover, press-key, screenshot, scroll-to, "
    "wait-for-selector, http-request, expect-visual-match. "
    "Prefer semantic intents ('primary checkout button') over brittle "
    "selectors. Output nothing except the JSON array."
)


@router.post("/cases/generate")
async def generate_case(
    body: CaseGenerationRequest,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Generate a draft TestCase via the LLM.

    Phase D — mode selection:
      • `direct`  → create the case immediately. Allowed only when the caller
                    is a human (JWT) with editor role. API keys cannot use this.
      • `propose` → enqueue a CaseProposal for human review. Default for API
                    key callers; available to humans who want a review trail.

    The response shape differs by mode: `direct` returns a TestCaseRead;
    `propose` returns a CaseProposalRead-like dict.
    """
    suite = await session.get(TestSuite, body.test_suite_id)
    if not suite:
        raise HTTPException(status_code=404, detail="Test suite not found")
    if not await access_service.has_project_access(
        principal.user.id, suite.project_id, session, min_role="editor"
    ):
        raise HTTPException(status_code=403, detail="Editor access required")

    if llm_provider.name == "null":
        raise HTTPException(
            status_code=400,
            detail="No LLM provider configured. Set OPENAI_API_KEY or ANTHROPIC_API_KEY.",
        )

    # Resolve mode + enforce that API keys never write directly.
    mode = (body.mode or ("propose" if principal.is_api_caller else "direct")).lower()
    if mode not in ("direct", "propose"):
        raise HTTPException(status_code=400, detail="mode must be 'direct' or 'propose'")
    if mode == "direct" and principal.is_api_caller:
        raise HTTPException(
            status_code=403,
            detail="API keys cannot create cases directly. Use mode='propose'.",
        )

    # Resolve the project's workspace + enforce budget cap.
    project = await session.get(Project, suite.project_id)
    if project:
        await _check_and_consume_ai_budget(project.workspace_id, session)

    prompt = (
        f"Description: {body.description}\n"
        f"Target URL: {body.target_url or '(none provided)'}\n\n"
        "Generate the steps needed to verify the described user journey. "
        "Begin with a `goto` step if a target URL is provided. End with an "
        "`expect-visible` or `expect-text` step that proves the journey "
        "succeeded."
    )
    raw = llm_provider.complete(prompt, system=_GENERATION_SYSTEM, max_tokens=1500)
    steps = _parse_steps_payload(raw)
    if not steps:
        raise HTTPException(
            status_code=502,
            detail=f"LLM returned no parseable steps. Raw response: {raw[:200]}",
        )

    case_name = body.case_name or _slug_from_description(body.description)
    code_paths = body.code_paths or []

    if mode == "direct":
        case = TestCase(
            name=case_name,
            steps=steps,
            test_suite_id=body.test_suite_id,
            project_id=suite.project_id,
            created_by_id=principal.user.id,
            updated_by_id=principal.user.id,
            code_paths=code_paths,
            is_ai_authored=True,
            ai_confidence=0.7,
            last_human_reviewed_at=datetime.utcnow(),
            last_human_reviewed_by_id=principal.user.id,
        )
        session.add(case)
        await session.commit()
        await session.refresh(case)
        return TestCaseRead(**case.model_dump())

    # mode == "propose"
    proposal = CaseProposal(
        project_id=suite.project_id,
        test_suite_id=body.test_suite_id,
        action=CaseProposalAction.CREATE,
        payload={
            "name": case_name,
            "steps": steps,
            "code_paths": code_paths,
        },
        rationale=body.description[:500],
        ai_confidence=0.7,
        agent_id=principal.agent_id,
    )
    session.add(proposal)
    await session.commit()
    await session.refresh(proposal)
    return {
        "mode": "propose",
        "proposal_id": proposal.id,
        "status": proposal.status,
        "review_endpoint": f"/api/case-proposals/{proposal.id}/accept",
    }


# ---------------------------------------------------------------------------
# Test-from-OpenAPI
# ---------------------------------------------------------------------------

@router.post("/cases/from-openapi")
async def cases_from_openapi(
    body: CaseFromOpenAPIRequest,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    suite = await session.get(TestSuite, body.test_suite_id)
    if not suite:
        raise HTTPException(status_code=404, detail="Test suite not found")
    if not await access_service.has_project_access(
        principal.user.id, suite.project_id, session, min_role="editor"
    ):
        raise HTTPException(status_code=403, detail="Editor access required")

    schema = body.schema_inline
    if not schema and body.schema_url:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(body.schema_url)
            if resp.status_code >= 300:
                raise HTTPException(
                    status_code=502, detail=f"Could not fetch schema_url: {resp.status_code}"
                )
            try:
                schema = resp.json()
            except Exception:
                # try YAML
                import yaml  # type: ignore
                schema = yaml.safe_load(resp.text)
    if not isinstance(schema, dict):
        raise HTTPException(status_code=400, detail="schema_url/schema_inline did not yield a dict")

    base_url = body.base_url or _detect_base_url(schema)
    paths = schema.get("paths") or {}
    if not isinstance(paths, dict):
        raise HTTPException(status_code=400, detail="Schema has no `paths` object")

    requested = set(body.operations or [])
    created: List[Dict[str, Any]] = []

    for path_str, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            op_id = (op or {}).get("operationId") or f"{method.upper()} {path_str}"
            if requested and op_id not in requested:
                continue

            steps = _steps_for_openapi_op(base_url, path_str, method, op or {})
            case = TestCase(
                name=f"{method.upper()} {path_str}",
                steps=steps,
                test_suite_id=body.test_suite_id,
                project_id=suite.project_id,
                created_by_id=principal.user.id,
                updated_by_id=principal.user.id,
            )
            session.add(case)
            created.append({"operation_id": op_id, "name": case.name})

    await session.commit()
    return {"created": len(created), "cases": created}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_steps_payload(raw: str) -> List[Dict[str, Any]]:
    if not raw:
        return []
    raw = raw.strip()
    # Strip ``` fences if present
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    # Extract first JSON array
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        steps = json.loads(raw[start: end + 1])
    except Exception:
        return []
    if not isinstance(steps, list):
        return []
    cleaned = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        s.setdefault("id", str(uuid.uuid4()))
        if "type" not in s:
            continue
        cleaned.append(s)
    return cleaned


def _slug_from_description(description: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", description.strip()).strip("-")
    return slug[:64] or "generated-case"


def _detect_base_url(schema: Dict[str, Any]) -> str:
    servers = schema.get("servers")
    if isinstance(servers, list) and servers and isinstance(servers[0], dict):
        return servers[0].get("url", "")
    return ""


def _steps_for_openapi_op(
    base_url: str, path_str: str, method: str, op: Dict[str, Any]
) -> List[Dict[str, Any]]:
    url = (base_url.rstrip("/") + path_str) if base_url else path_str
    request_step = {
        "id": str(uuid.uuid4()),
        "type": "http-request",
        "value": url,
        "intent": op.get("summary") or f"Call {method.upper()} {path_str}",
        "params": {
            "method": method.upper(),
            "expected_status": _first_success_status(op),
        },
    }
    assert_step = {
        "id": str(uuid.uuid4()),
        "type": "feed-check",
        "intent": "Assert response shape matches the documented schema",
        "params": {
            "assertions": [
                {"path": "$.status", "op": "lt", "value": 400},
            ]
        },
    }
    return [request_step, assert_step]


def _first_success_status(op: Dict[str, Any]) -> int:
    responses = op.get("responses", {})
    for code in ("200", "201", "204"):
        if code in responses:
            return int(code)
    # Fallback: lowest 2xx
    for k in sorted(responses.keys()):
        if str(k).startswith("2"):
            try:
                return int(k)
            except Exception:
                continue
    return 200
