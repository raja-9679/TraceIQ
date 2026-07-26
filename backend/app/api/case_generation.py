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
from html.parser import HTMLParser

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
from app.services.llm_usage import llm_call_context

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

    # Probe the target URL so selectors/assertions are grounded in what the
    # page/endpoint actually serves, instead of hallucinated from the
    # description alone.
    kind, target_context = ("", "")
    if body.target_url:
        kind, target_context = await _fetch_target_context(body.target_url)

    if kind == "html":
        prompt = (
            f"Description: {body.description}\n"
            f"Target URL: {body.target_url}\n\n"
            "Below are the interactive elements extracted from a live fetch of the "
            "target page. Build selectors ONLY from these real elements — prefer "
            "#id, [data-testid=...], [name=...], then text-based selectors. Never "
            "invent an id or class that is not listed.\n\n"
            f"{target_context}\n\n"
            "Generate the steps needed to verify the described user journey. "
            "Begin with a `goto` step to the target URL. End with an "
            "`expect-visible` or `expect-text` step that proves the journey succeeded."
        )
    elif kind in ("json", "feed"):
        step_type = "http-request" if kind == "json" else "feed-check"
        prompt = (
            f"Description: {body.description}\n"
            f"Target URL: {body.target_url}\n\n"
            f"The target URL serves {'JSON (an API endpoint)' if kind == 'json' else 'an XML/RSS/Atom feed'}, "
            "not an HTML page. Below is the actual response from a live probe. "
            f"Generate `{step_type}` steps whose assertions are grounded in this real "
            "response shape — assert on fields/paths that actually exist. Do NOT "
            "generate browser steps like goto/click/fill.\n\n"
            f"{target_context}"
        )
    else:
        prompt = (
            f"Description: {body.description}\n"
            f"Target URL: {body.target_url or '(none provided)'}\n\n"
            "Generate the steps needed to verify the described user journey. "
            "Begin with a `goto` step if a target URL is provided. End with an "
            "`expect-visible` or `expect-text` step that proves the journey "
            "succeeded."
        )
    with llm_call_context(
        feature="case_generation",
        workspace_id=project.workspace_id if project else None,
        project_id=suite.project_id,
    ):
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
            # Phase E: provenance.
            created_by_agent_id=principal.agent_id,
            agent_session_id=principal.agent_session_id,
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
        # Phase E: provenance.
        created_by_agent_id=principal.agent_id,
        agent_session_id=principal.agent_session_id,
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
                # Phase E: stamp provenance.
                created_by_agent_id=principal.agent_id,
                agent_session_id=principal.agent_session_id,
            )
            session.add(case)
            created.append({"operation_id": op_id, "name": case.name})

    await session.commit()
    return {"created": len(created), "cases": created}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _fetch_target_context(url: str) -> tuple:
    """Probe the target URL and return (kind, context) for prompt grounding.

    kind: "html" (distilled interactive DOM) | "json" (pretty response) |
    "feed" (raw XML head) | "" (probe failed — caller falls back to the
    ungrounded prompt).
    """
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, verify=False) as client:
            resp = await client.get(url, headers={"User-Agent": "TraceIQ-CaseGenerator/1.0"})
    except Exception as exc:  # noqa: BLE001 — probe is best-effort
        print(f"[case-gen] probe of {url} failed: {exc}")
        return "", ""

    ctype = resp.headers.get("content-type", "").lower()
    body = resp.text or ""
    head = body.lstrip()[:1]

    if "json" in ctype or (head in ("{", "[") and "html" not in ctype):
        try:
            pretty = json.dumps(resp.json(), indent=1, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            pretty = body
        return "json", f"HTTP {resp.status_code}  content-type: {ctype}\n{pretty[:3000]}"
    if any(t in ctype for t in ("xml", "rss", "atom")) or body.lstrip().startswith("<?xml"):
        return "feed", f"HTTP {resp.status_code}  content-type: {ctype}\n{body[:3000]}"
    if "html" in ctype or "<html" in body[:1000].lower():
        distilled = _distill_dom(body)
        # Empty distillation (e.g. a JS-only shell page) → fall back to the
        # ungrounded prompt rather than promising elements we don't have.
        return ("html", distilled) if distilled else ("", "")
    return "", ""


class _InteractiveElementCollector(HTMLParser):
    """Extracts interactive/landmark elements with the attributes that make
    good selectors, so the LLM grounds selectors in the real page."""

    _CAPTURE = {"a", "button", "input", "select", "textarea", "form", "label",
                "h1", "h2", "h3"}
    _ATTRS = ("id", "name", "type", "placeholder", "data-testid", "role",
              "aria-label", "value", "href", "action", "for")
    _MAX_ELEMENTS = 120

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: List[str] = []
        self._open: Optional[Dict[str, Any]] = None
        self._skip_depth = 0  # inside <script>/<style>

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style", "svg", "noscript"):
            self._skip_depth += 1
            return
        if self._skip_depth or len(self.lines) >= self._MAX_ELEMENTS or tag not in self._CAPTURE:
            return
        attr_map = dict(attrs)
        parts = [tag]
        for key in self._ATTRS:
            val = (attr_map.get(key) or "").strip()
            if val:
                parts.append(f'{key}="{val[:80]}"')
        classes = (attr_map.get("class") or "").split()
        if classes:
            parts.append(f'class="{" ".join(classes[:3])}"')
        self._flush()
        self._open = {"desc": " ".join(parts), "text": []}
        # inputs are void elements — no text will follow
        if tag == "input":
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "svg", "noscript"):
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in self._CAPTURE:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._open is not None and not self._skip_depth:
            text = data.strip()
            if text:
                self._open["text"].append(text)

    def _flush(self) -> None:
        if self._open is None:
            return
        text = " ".join(self._open["text"])[:100]
        line = f'<{self._open["desc"]}>'
        if text:
            line += f" text={text!r}"
        self.lines.append(line)
        self._open = None

    def close(self) -> None:  # noqa: D102
        self._flush()
        super().close()


def _distill_dom(html: str) -> str:
    """Reduce a page to a compact list of its interactive elements (~5KB max)
    so it fits small-model context windows."""
    collector = _InteractiveElementCollector()
    try:
        collector.feed(html)
        collector.close()
    except Exception as exc:  # noqa: BLE001 — malformed HTML must not 500 the endpoint
        print(f"[case-gen] DOM distillation failed: {exc}")
    if not collector.lines:
        return ""
    out = "\n".join(collector.lines)
    return out[:5000]


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
