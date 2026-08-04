"""Security: passive per-run findings + project-level ZAP scans."""
from __future__ import annotations

from typing import Optional

from traceiq_mcp.app import mcp
from traceiq_mcp.client import new_client
from traceiq_mcp.schemas.security import (
    RunSecurityFindings,
    ScanDiff,
    SecurityScan,
    SecurityScanList,
)


@mcp.tool()
async def run_security_scan(run_id: int) -> RunSecurityFindings:
    """Passive security scan of a run's already-captured responses (headers,
    network events). Read-only against the target — it re-analyses recorded
    data only. Returns the findings immediately."""
    data = await new_client().run_security_scan(run_id)
    return RunSecurityFindings.model_validate(data)


@mcp.tool()
async def get_run_security_findings(run_id: int) -> RunSecurityFindings:
    """Stored security findings for a run (populated at finalize or by
    run_security_scan), sorted most-severe first."""
    data = await new_client().get_run_security_findings(run_id)
    return RunSecurityFindings.model_validate(data)


@mcp.tool()
async def start_project_security_scan(
    project_id: int,
    target_url: str,
    authorized: bool,
    scan_type: str = "baseline",
    authenticated: bool = False,
    openapi_url: Optional[str] = None,
) -> SecurityScan:
    """Start a ZAP security scan of a live target. `authorized=true` is a
    required attestation that you are allowed to scan this target; the host
    must also be on the project's authorized-domain allowlist, and 'active'
    scans need the project + workspace opt-ins. Async: poll
    get_security_scan until status is completed."""
    data = await new_client().start_project_security_scan(
        project_id, target_url, authorized, scan_type, authenticated,
        openapi_url)
    return SecurityScan.model_validate(data)


@mcp.tool()
async def list_security_scans(project_id: int) -> SecurityScanList:
    """List a project's ZAP security scans, newest first."""
    data = await new_client().list_security_scans(project_id)
    return SecurityScanList(items=data)


@mcp.tool()
async def get_security_scan(scan_id: int) -> SecurityScan:
    """One security scan with its findings sorted by severity."""
    data = await new_client().get_security_scan(scan_id)
    return SecurityScan.model_validate(data)


@mcp.tool()
async def get_security_scan_diff(scan_id: int) -> ScanDiff:
    """Scan-over-scan comparison against the previous completed scan of the
    same project+target: which findings are new, resolved, or persisting.
    Use after a fix to prove a finding is actually gone."""
    data = await new_client().get_security_scan_diff(scan_id)
    return ScanDiff.model_validate(data)
