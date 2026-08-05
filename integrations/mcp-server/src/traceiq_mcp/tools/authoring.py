"""Case authoring: the proposal queue, LLM generation, and reference docs.

Write policy (do not try to bypass it): every case create/update/delete —
and every suite-settings change — goes through the CaseProposal queue.
Proposals with ai_confidence at or above the workspace's
auto_apply_threshold merge immediately (case CREATE/UPDATE only; deletes
and suite-settings changes always wait for a human). API keys can never
accept proposals.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from traceiq_mcp.app import mcp
from traceiq_mcp.client import new_client
from traceiq_mcp.schemas.authoring import (
    AuthoringGuide,
    BulkProposalResult,
    GeneratedProposal,
    Proposal,
    ProposalList,
    StepTypeCatalog,
)


@mcp.tool()
async def get_authoring_guide() -> AuthoringGuide:
    """TraceIQ's authoritative authoring reference (AGENT_GUIDE.md). Read this
    at the start of every session before proposing anything: step-type shapes,
    suite conventions, code_paths discipline, auth setup, common pitfalls."""
    data = await new_client().get_authoring_guide()
    return AuthoringGuide.model_validate(data)


@mcp.tool()
async def describe_step_types() -> StepTypeCatalog:
    """Catalogue of every step type the runner supports (web + api + load +
    mobile), with expected params, an example, and gotchas. Use whenever
    constructing a `steps` array."""
    data = await new_client().describe_step_types()
    return StepTypeCatalog.model_validate(data)


@mcp.tool()
async def propose_create_case(
    project_id: int,
    test_suite_id: int,
    name: str,
    steps: List[Dict[str, Any]],
    code_paths: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    priority: Optional[str] = None,
    executor: Optional[str] = None,
    rationale: Optional[str] = None,
    ai_confidence: float = 0.5,
) -> Proposal:
    """Propose a new TestCase (goes to the human-review queue; may auto-apply
    if ai_confidence clears the workspace threshold). ALWAYS include
    code_paths so the case participates in future impact analysis. Selectors
    invented without observing the real app are notoriously brittle — ground
    them in a crawl or the app's actual source.

    executor picks WHICH worker runs the case — it must match the steps:
    "ui_playwright" (default; browser + http-request steps),
    "mobile_appium" (mobile-* steps; needs an app build pinned to the run
    via app_build_id). Set it EXPLICITLY for mobile cases. If omitted, the
    server infers mobile_appium when every step type starts with "mobile-";
    a mixed or wrong pairing means workers skip steps they don't implement.

    tags are free-form labels used to select subsets at run time (e.g.
    run_suite(tags=["smoke"])) — tag every case you create so humans can
    slice runs later. priority is one of "critical" | "high" | "medium" |
    "low".

    Headers/auth: http-request steps take per-step params.headers, but if
    every case in the suite needs the same Authorization header, put it in
    the SUITE's settings instead (create_suite settings=... for new suites,
    propose_update_suite_settings for existing ones) and keep the steps
    clean."""
    data = await new_client().propose_case(
        project_id=project_id, action="create", test_suite_id=test_suite_id,
        payload={"name": name, "steps": steps, "code_paths": code_paths or [],
                 "tags": tags or [], "priority": priority,
                 "executor": executor},
        rationale=rationale, ai_confidence=ai_confidence)
    return Proposal.model_validate(data)


@mcp.tool()
async def propose_update_case(
    project_id: int,
    target_case_id: int,
    patch: Dict[str, Any],
    rationale: Optional[str] = None,
    ai_confidence: float = 0.5,
) -> Proposal:
    """Propose updating an existing TestCase. `patch` holds ONLY the fields to
    change — supported keys: name, steps, code_paths, tags, priority,
    executor; other keys are ignored on apply. `steps` is replaced wholesale
    (not merged), so include the FULL steps array even when editing one step.
    Fetch the case with get_case first so the patch is built against current
    state."""
    data = await new_client().propose_case(
        project_id=project_id, action="update", target_case_id=target_case_id,
        payload=patch, rationale=rationale, ai_confidence=ai_confidence)
    return Proposal.model_validate(data)


@mcp.tool()
async def propose_delete_case(project_id: int, target_case_id: int,
                              rationale: str) -> Proposal:
    """Propose deleting an obsolete TestCase. Delete proposals ALWAYS wait for
    a human (never auto-applied) — say clearly in the rationale why the case
    no longer reflects the application."""
    data = await new_client().propose_case(
        project_id=project_id, action="delete", target_case_id=target_case_id,
        payload={"reason": rationale}, rationale=rationale, ai_confidence=1.0)
    return Proposal.model_validate(data)


@mcp.tool()
async def propose_update_suite_settings(
    project_id: int,
    test_suite_id: int,
    settings: Optional[Dict[str, Any]] = None,
    inherit_settings: Optional[bool] = None,
    merge: bool = True,
    rationale: Optional[str] = None,
) -> Proposal:
    """Propose changing a TestSuite's settings — the suite-level config every
    case in it (and in child suites that inherit) receives at run time. Use
    this instead of repeating the same headers on every http-request step.

    ALWAYS human-reviewed: unlike case proposals, suite settings are never
    auto-applied regardless of workspace threshold, because headers here are
    sent to the app under test with real credentials on every run. Write a
    rationale a reviewer can verify (what you're setting and why).

    `settings` shape — include only the keys you want to change:

      {
        "headers": {"Authorization": "Bearer {{token}}", "X-Env": "staging"},
            # merged into every http-request step; a step's own
            # params.headers win on conflict
        "params":  {"tenant": "acme"},          # default query params
        "allowed_domains": ["api.example.com"], # extra hosts steps may touch
        "domain_settings": {"api.example.com": {"headers": {...}}},
            # per-host overrides, e.g. a different token for the API host
        "browsers": ["chromium", "firefox"],    # default execution matrix
        "devices":  ["iPhone 14"]
      }

    merge=True (default): your keys are merged over the suite's existing
    settings (headers/params/domain_settings merge per key, your values win;
    browsers/devices replace wholesale). merge=False replaces the ENTIRE
    settings blob — read get_suite.effective_settings first if you use it.

    inherit_settings toggles whether the suite also receives its parent
    chain's settings (child wins on conflict). Pass False to isolate the
    suite from parent headers/params.

    Do not put long-lived secrets in plain header values when the deployment
    has environments configured — prefer environment_id on run_suite for
    per-env secrets."""
    data = await new_client().propose_case(
        project_id=project_id, action="update_suite_settings",
        test_suite_id=test_suite_id,
        payload={"settings": settings, "inherit_settings": inherit_settings,
                 "merge": merge},
        rationale=rationale, ai_confidence=0.0)
    return Proposal.model_validate(data)


@mcp.tool()
async def bulk_propose_cases(project_id: int,
                             proposals: List[Dict[str, Any]]) -> BulkProposalResult:
    """Submit MANY case proposals in one round-trip. Best-effort: one bad item
    doesn't reject the rest — each returns per-index status. Item shape:
    {project_id, action: create|update|delete|move, test_suite_id?,
    target_case_id?, payload, rationale?, ai_confidence?}. Use when generating
    coverage for a whole feature at once."""
    data = await new_client().bulk_propose_cases(project_id, proposals)
    return BulkProposalResult.model_validate(data)


@mcp.tool()
async def list_case_proposals(project_id: Optional[int] = None,
                              status: str = "pending",
                              limit: int = 100) -> ProposalList:
    """List CaseProposals (your pending work queue). Check before re-submitting
    to avoid duplicates; status: pending | accepted | rejected."""
    data = await new_client().list_case_proposals(project_id, status, limit)
    return ProposalList(items=data)


@mcp.tool()
async def generate_case_proposal(
    description: str,
    test_suite_id: int,
    target_url: Optional[str] = None,
    case_name: Optional[str] = None,
    code_paths: Optional[List[str]] = None,
) -> GeneratedProposal:
    """Have TraceIQ's server-side LLM draft a TestCase from a natural-language
    description and enqueue it as a proposal. Use when you need a starting
    draft; prefer authoring steps yourself when you know the app's DOM."""
    data = await new_client().generate_case_proposal(
        description, test_suite_id, target_url, case_name, code_paths)
    return GeneratedProposal.model_validate(data)
