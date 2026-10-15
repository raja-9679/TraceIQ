"""Security-finding workflow: owners, SLA due dates, status transitions,
carry-forward across scans, automatic re-open and retest verification (WP7).

Findings are written per scan (each ZAP scan, each run's passive and leak
pass). The *workflow* is per logical finding — (project, scan_type,
fingerprint) — so it lives on the newest row and older rows point at it via
`superseded_by_id`. `ingest()` is the single place that links a new occurrence
to its predecessor and decides what state it inherits:

* owner, due date and an unexpired risk acceptance carry over — the SLA clock
  starts at FIRST detection, so a finding seen every night does not get a fresh
  30 days every night;
* `false_positive` carries over (the old ZAP-only behaviour, now for all types);
* a finding marked fixed (pending or verified) that shows up again is
  **re-opened** — that is the whole point of tracking fixes;
* an accepted risk whose acceptance has expired re-opens too.

`verify_fixes()` is the other half of retest-on-fix: a finding in
`fixed_pending_verification` becomes `verified_fixed` only when a LATER scan of
the same target, of the same kind, completes without producing its fingerprint.
A human cannot set `verified_fixed` — "I fixed it" is a claim, the rescan is the
evidence.

Pure rules at the top (unit-tested without a DB); session helpers below take a
*sync* SQLModel session — async callers use `await session.run_sync(...)`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

STATUSES = (
    "open", "in_progress", "fixed_pending_verification", "verified_fixed",
    "accepted_risk", "false_positive",
)
#: Statuses that still need someone to act — the ones an SLA applies to.
ACTIONABLE = frozenset({"open", "in_progress"})
#: Severities the SLA knows. `info` deliberately has no due date.
DEFAULT_FINDING_SLA: Dict[str, int] = {"critical": 7, "high": 30, "medium": 90, "low": 180}
MAX_RISK_ACCEPTANCE_DAYS = 365

#: What a person may move a finding to from each state. `verified_fixed` is
#: absent everywhere on purpose: only a rescan can verify a fix.
MANUAL_TRANSITIONS: Dict[str, frozenset] = {
    "open": frozenset({"in_progress", "fixed_pending_verification", "accepted_risk", "false_positive"}),
    "in_progress": frozenset({"open", "fixed_pending_verification", "accepted_risk", "false_positive"}),
    "fixed_pending_verification": frozenset({"open", "in_progress"}),
    "verified_fixed": frozenset({"open"}),
    "accepted_risk": frozenset({"open", "in_progress"}),
    "false_positive": frozenset({"open"}),
}

#: Old vocabulary, accepted on input so an older client keeps working.
LEGACY_STATUS = {"acknowledged": "in_progress", "resolved": "fixed_pending_verification"}


class TransitionError(ValueError):
    """A requested status change the workflow does not allow."""


def normalize_status(status: str) -> str:
    s = (status or "").strip().lower()
    return LEGACY_STATUS.get(s, s)


def check_transition(current: str, target: str, *, justification: Optional[str] = None,
                     expires_at: Optional[datetime] = None,
                     now: Optional[datetime] = None) -> None:
    """Raise TransitionError unless a person may move current → target."""
    now = now or datetime.utcnow()
    current = normalize_status(current) if current else "open"
    if target not in STATUSES:
        raise TransitionError(f"status must be one of {', '.join(STATUSES)}")
    if target == current:
        raise TransitionError(f"finding is already {target}")
    if target == "verified_fixed":
        raise TransitionError(
            "verified_fixed is set by a rescan that no longer finds the issue; "
            "mark it fixed_pending_verification instead")
    if target not in MANUAL_TRANSITIONS.get(current, frozenset()):
        raise TransitionError(f"cannot move a finding from {current} to {target}")
    if target == "accepted_risk":
        if not (justification or "").strip():
            raise TransitionError("accepting a risk requires a justification")
        if expires_at is None:
            raise TransitionError("accepting a risk requires an expiry date")
        if expires_at <= now:
            raise TransitionError("risk acceptance expiry must be in the future")
        if expires_at > now + timedelta(days=MAX_RISK_ACCEPTANCE_DAYS):
            raise TransitionError(
                f"risk acceptance can last at most {MAX_RISK_ACCEPTANCE_DAYS} days — "
                "re-accept it at expiry if it still applies")


def resolve_sla(stored: Optional[dict]) -> Dict[str, int]:
    out = dict(DEFAULT_FINDING_SLA)
    if isinstance(stored, dict):
        for sev, days in stored.items():
            if sev in DEFAULT_FINDING_SLA and isinstance(days, int) and days > 0:
                out[sev] = days
    return out


def validate_sla(payload: Any) -> Dict[str, int]:
    if not isinstance(payload, dict):
        raise ValueError("SLA must be an object of severity → days")
    out: Dict[str, int] = {}
    for sev, days in payload.items():
        if sev not in DEFAULT_FINDING_SLA:
            raise ValueError(f"unknown severity {sev!r}; expected one of "
                             f"{', '.join(DEFAULT_FINDING_SLA)}")
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 3650:
            raise ValueError(f"{sev}: days must be a whole number between 1 and 3650")
        out[sev] = days
    return out


def due_date(detected_at: datetime, severity: str, sla: Dict[str, int]) -> Optional[datetime]:
    days = sla.get((severity or "").lower())
    return detected_at + timedelta(days=days) if days else None


def is_overdue(finding: Any, now: Optional[datetime] = None) -> bool:
    now = now or datetime.utcnow()
    due = getattr(finding, "due_at", None)
    return bool(due and due < now and getattr(finding, "status", "open") in ACTIONABLE)


#: Fields a new occurrence inherits from its predecessor.
CARRIED_FIELDS = ("assignee_id", "due_at", "risk_justification", "risk_expires_at",
                  "risk_approved_by_id", "risk_approved_at")


@dataclass
class Carry:
    status: str
    fields: Dict[str, Any]
    reopened: Optional[str] = None   # reason, when the carry re-opened it


def carry_forward(prior: Any, now: Optional[datetime] = None) -> Carry:
    """State a new occurrence of `prior` (row or dict) starts in."""
    now = now or datetime.utcnow()

    def get(name):
        return prior.get(name) if isinstance(prior, dict) else getattr(prior, name, None)

    status = normalize_status(get("status") or "open")
    fields = {name: get(name) for name in CARRIED_FIELDS}
    reopened = None
    if status in ("fixed_pending_verification", "verified_fixed"):
        status, reopened = "open", "seen_again"
    elif status == "accepted_risk":
        expires = get("risk_expires_at")
        if not expires or expires <= now:
            status, reopened = "open", "risk_acceptance_expired"
    if status != "accepted_risk":
        for name in ("risk_justification", "risk_expires_at", "risk_approved_by_id", "risk_approved_at"):
            fields[name] = None
    return Carry(status=status, fields=fields, reopened=reopened)


def state_snapshot(row: Any) -> Dict[str, Any]:
    """The workflow state of a row about to be deleted (re-scan of one run)."""
    return {"status": row.status, **{n: getattr(row, n, None) for n in CARRIED_FIELDS}}


# ---------------------------------------------------------------------------
# Session helpers (sync)
# ---------------------------------------------------------------------------

def _workspace_for_project(session: Any, project_id: Optional[int]):
    from app.models import Project, Workspace
    if not project_id:
        return None
    project = session.get(Project, project_id)
    return session.get(Workspace, project.workspace_id) if project else None


def _audit(session: Any, finding: Any, action: str, changes: dict, workspace_id: Optional[int]):
    from app.services.audit import record_sync
    record_sync(session, entity_type="security_finding", entity_id=finding.id,
                action=action, workspace_id=workspace_id, changes=changes,
                actor_type="system", actor_label="finding-lifecycle")


def ingest(session: Any, new_rows: Sequence[Any], *, project_id: Optional[int],
           scan_type: str, now: Optional[datetime] = None,
           replaced: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, int]:
    """Link freshly-added finding rows to their predecessors. Caller commits.

    `replaced` maps fingerprint → state_snapshot() of rows the caller deleted
    in this transaction (re-scanning the same run replaces its findings), so
    their workflow state is inherited rather than lost.
    """
    from sqlmodel import select
    from app.models import SecurityFinding

    now = now or datetime.utcnow()
    session.flush()   # ids for superseded_by_id
    workspace = _workspace_for_project(session, project_id)
    workspace_id = workspace.id if workspace else None
    sla = resolve_sla(workspace.finding_sla_days if workspace else None)
    new_ids = {r.id for r in new_rows}
    stats = {"carried": 0, "reopened": 0, "new": 0}

    for row in new_rows:
        prior = None
        if row.fingerprint and replaced and row.fingerprint in replaced:
            prior = replaced[row.fingerprint]
        elif row.fingerprint and project_id:
            prior = session.exec(
                select(SecurityFinding).where(
                    SecurityFinding.project_id == project_id,
                    SecurityFinding.scan_type == scan_type,
                    SecurityFinding.fingerprint == row.fingerprint,
                    SecurityFinding.superseded_by_id == None,  # noqa: E711
                    SecurityFinding.id.not_in(new_ids),
                ).order_by(SecurityFinding.id.desc())).first()
        if prior is None:
            row.status = normalize_status(row.status or "open")
            row.due_at = row.due_at or due_date(row.created_at or now, row.severity, sla)
            stats["new"] += 1
            session.add(row)
            continue

        carry = carry_forward(prior, now)
        row.status = carry.status
        for name, value in carry.fields.items():
            setattr(row, name, value)
        if row.due_at is None:
            row.due_at = due_date(row.created_at or now, row.severity, sla)
        session.add(row)
        if not isinstance(prior, dict):
            prior.superseded_by_id = row.id
            session.add(prior)
        stats["carried"] += 1
        if carry.reopened:
            stats["reopened"] += 1
            session.flush()
            _audit(session, row, "reopen",
                   {"from": normalize_status(prior["status"] if isinstance(prior, dict) else prior.status),
                    "to": "open", "reason": carry.reopened,
                    "previous_finding_id": None if isinstance(prior, dict) else prior.id,
                    "fingerprint": row.fingerprint}, workspace_id)
    return stats


def verify_fixes(session: Any, *, project_id: Optional[int], scan_type: str,
                 produced: Iterable[str], in_scope: Callable[[Any], bool],
                 evidence: Dict[str, Any], now: Optional[datetime] = None) -> List[int]:
    """Mark pending fixes whose fingerprint a completed, in-scope scan did NOT
    produce as verified_fixed. Returns the ids verified. Caller commits.

    `in_scope(finding)` answers "did this scan look where the finding was" —
    a scan of a different target, or a run that never exercised the page,
    proves nothing about the fix.
    """
    from sqlmodel import select
    from app.models import SecurityFinding

    if not project_id:
        return []
    now = now or datetime.utcnow()
    produced = set(p for p in produced if p)
    pending = session.exec(select(SecurityFinding).where(
        SecurityFinding.project_id == project_id,
        SecurityFinding.scan_type == scan_type,
        SecurityFinding.status == "fixed_pending_verification",
        SecurityFinding.superseded_by_id == None,  # noqa: E711
    )).all()
    workspace = _workspace_for_project(session, project_id)
    verified: List[int] = []
    for finding in pending:
        if not finding.fingerprint or finding.fingerprint in produced:
            continue
        if not in_scope(finding):
            continue
        finding.status = "verified_fixed"
        finding.verified_at = now
        finding.resolved_at = finding.resolved_at or now
        session.add(finding)
        _audit(session, finding, "verified_fixed",
               {"from": "fixed_pending_verification", "to": "verified_fixed", **evidence},
               workspace.id if workspace else None)
        verified.append(finding.id)
    return verified


def reopen_expired_risks(session: Any, now: Optional[datetime] = None) -> int:
    """Accepted risks whose acceptance lapsed go back to open. Caller commits."""
    from sqlmodel import select
    from app.models import SecurityFinding

    now = now or datetime.utcnow()
    rows = session.exec(select(SecurityFinding).where(
        SecurityFinding.status == "accepted_risk",
        SecurityFinding.risk_expires_at != None,  # noqa: E711
        SecurityFinding.risk_expires_at <= now,
        SecurityFinding.superseded_by_id == None,  # noqa: E711
    )).all()
    for finding in rows:
        expired = finding.risk_expires_at
        finding.status = "open"
        session.add(finding)
        workspace = _workspace_for_project(session, finding.project_id)
        _audit(session, finding, "reopen",
               {"from": "accepted_risk", "to": "open", "reason": "risk_acceptance_expired",
                "risk_expired_at": expired.isoformat() if expired else None},
               workspace.id if workspace else None)
    return len(rows)


def origin_of(url: Optional[str]) -> Optional[str]:
    from urllib.parse import urlsplit
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else None


def run_origins(run: Any) -> set:
    """Origins a run actually loaded — the scope a passive pass can vouch for."""
    out = set()
    for ev in getattr(run, "network_events", None) or []:
        if isinstance(ev, dict):
            o = origin_of(ev.get("url"))
            if o:
                out.add(o)
    o = origin_of(getattr(run, "request_url", None))
    if o:
        out.add(o)
    return out


def partition_by_status(findings: Iterable[Any]) -> Tuple[int, int]:
    """(actionable, overdue) counts — the numbers a dashboard headline needs."""
    now = datetime.utcnow()
    actionable = overdue = 0
    for f in findings:
        if f.status in ACTIONABLE:
            actionable += 1
            if is_overdue(f, now):
                overdue += 1
    return actionable, overdue
