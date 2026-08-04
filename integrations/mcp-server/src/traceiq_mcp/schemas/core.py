"""Workspaces, projects, suites, cases."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from traceiq_mcp.schemas.common import TQModel


class Workspace(TQModel):
    id: int
    name: str
    description: Optional[str] = None


class WorkspaceList(TQModel):
    items: List[Workspace] = []


class Project(TQModel):
    id: int
    name: str
    description: Optional[str] = None
    workspace_id: Optional[int] = None
    access_level: Optional[str] = None


class ProjectList(TQModel):
    items: List[Project] = []


class SuiteSummary(TQModel):
    id: int
    name: str
    description: Optional[str] = None
    project_id: Optional[int] = None
    parent_id: Optional[int] = None
    execution_mode: Optional[str] = None
    total_test_cases: Optional[int] = None
    total_sub_modules: Optional[int] = None


class SuiteDetail(SuiteSummary):
    settings: Optional[Dict[str, Any]] = None
    inherit_settings: Optional[bool] = None
    effective_settings: Optional[Dict[str, Any]] = None


class SuiteList(TQModel):
    items: List[SuiteSummary] = []


class TestStep(TQModel):
    id: Optional[str] = None
    type: str
    selector: Optional[str] = None
    value: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    intent: Optional[str] = None


class CaseSummary(TQModel):
    id: int
    name: str
    test_suite_id: Optional[int] = None
    project_id: Optional[int] = None
    executor: Optional[str] = None
    tags: List[str] = []
    priority: Optional[str] = None
    is_ai_authored: bool = False
    code_paths: List[str] = []
    last_validated_commit: Optional[str] = None
    last_validated_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CaseList(TQModel):
    items: List[CaseSummary] = []
    total: int = 0
    limit: Optional[int] = None
    offset: Optional[int] = None


class CaseDetail(TQModel):
    id: int
    name: str
    steps: List[TestStep] = []
    executor: Optional[str] = None
    raw_script: Optional[str] = None
    test_suite_id: Optional[int] = None
    project_id: Optional[int] = None
    code_paths: List[str] = []
    tags: List[str] = []
    priority: Optional[str] = None
    dataset: Optional[List[Dict[str, Any]]] = None
    run_matrix: Optional[Dict[str, Any]] = None
    is_ai_authored: bool = False
    ai_confidence: Optional[float] = None
    last_human_reviewed_at: Optional[datetime] = None
    last_validated_commit: Optional[str] = None
    last_validated_at: Optional[datetime] = None
    is_auth_setup: bool = False
    use_auth_session: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
