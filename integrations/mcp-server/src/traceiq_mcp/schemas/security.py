"""Security scans (ZAP + passive) and findings."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from traceiq_mcp.schemas.common import TQModel


class SecurityFinding(TQModel):
    id: int
    run_id: Optional[int] = None
    scan_id: Optional[int] = None
    scan_type: str
    category: str
    severity: str
    title: str
    description: Optional[str] = None
    evidence: Optional[str] = None
    target_url: Optional[str] = None
    status: str = "open"
    created_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None


class RunSecurityFindings(TQModel):
    """Passive findings for a single run."""
    run_id: int
    scan_type: str = "passive"
    counts: Dict[str, int] = {}
    findings: List[SecurityFinding] = []


class SecurityScan(TQModel):
    id: int
    project_id: int
    target_url: str
    scan_type: str
    authenticated: bool = False
    status: str
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    counts: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    openapi_url: Optional[str] = None
    findings: List[SecurityFinding] = []


class SecurityScanList(TQModel):
    items: List[SecurityScan] = []


class ScanDiff(TQModel):
    """Scan-over-scan comparison; finding rows stay dicts (backend-versioned)."""
    scan_id: Optional[int] = None
    previous_scan_id: Optional[int] = None
    new: List[Dict[str, Any]] = []
    resolved: List[Dict[str, Any]] = []
    persisting: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {}
