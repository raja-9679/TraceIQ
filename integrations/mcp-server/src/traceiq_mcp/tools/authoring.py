"""Case authoring: the proposal queue, LLM generation, and reference docs.

Write policy (do not try to bypass it): every case create/update/delete goes
through the CaseProposal queue. Proposals with ai_confidence at or above the
workspace's auto_apply_threshold merge immediately (CREATE/UPDATE only);
everything else waits for a human. API keys can never accept proposals.
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
    rationale: Optional[str] = None,
    ai_confidence: float = 0.5,
) -> Proposal:
    """Propose a new TestCase (goes to the human-review queue; may auto-apply
    if ai_confidence clears the workspace threshold). ALWAYS include
    code_paths so the case participates in future impact analysis. Selectors
    invented without observing the real app are notoriously brittle — ground
    them in a crawl or the app's actual source."""
    data = await new_client().propose_case(
        project_id=project_id, action="create", test_suite_id=test_suite_id,
        payload={"name": name, "steps": steps, "code_paths": code_paths or []},
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
    """Propose updating an existing TestCase. `patch` holds only the fields to
    change (name, steps, code_paths, tags, priority). Fetch the case with
    get_case first so the patch is built against current state."""
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
