"""Proposals, generation, and the authoring reference (guide + step types)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from traceiq_mcp.schemas.common import TQModel


class Proposal(TQModel):
    id: int
    project_id: int
    test_suite_id: Optional[int] = None
    target_case_id: Optional[int] = None
    action: str
    status: str
    payload: Dict[str, Any] = {}
    rationale: Optional[str] = None
    ai_confidence: Optional[float] = None
    created_at: Optional[datetime] = None
    decided_at: Optional[datetime] = None
    decision_note: Optional[str] = None
    auto_applied: Optional[bool] = None


class ProposalList(TQModel):
    items: List[Proposal] = []


class BulkProposalItem(TQModel):
    index: int
    status: str  # "created" | "rejected"
    proposal_id: Optional[int] = None
    error: Optional[str] = None


class BulkProposalResult(TQModel):
    project_id: int
    submitted: int
    created: int
    rejected: int
    results: List[BulkProposalItem] = []


class GeneratedProposal(TQModel):
    """POST /api/cases/generate response (mode=propose is forced for agents)."""
    status: Optional[str] = None
    proposal_id: Optional[int] = None
    case_id: Optional[int] = None
    name: Optional[str] = None
    steps: List[Dict[str, Any]] = []
    detail: Optional[str] = None


class StepType(TQModel):
    type: str
    category: Optional[str] = None
    params: Dict[str, Any] = {}
    example: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None


class StepTypeCatalog(TQModel):
    step_types: List[StepType] = []
    total: int = 0
    last_updated: Optional[str] = None


class AuthoringGuide(TQModel):
    guide: str
    last_modified: Optional[str] = None
    size_chars: Optional[int] = None
