"""Control model (WP13a, C14 / decision C2): controls, evidence, exceptions.

A control is a claim a workspace makes ("admins use MFA", SOC 2 CC6.1) plus
the means to show it holds. Evidence comes from two places:

* **Automated evidence sources** — typed checks the product evaluates live
  against its own data (`SOURCES`). Each returns pass / fail / unknown with a
  reason and, for things that happen at a point in time (an access-review
  sign-off, a restore drill), *when* that happened.
* **Manual evidence** — a note and/or an uploaded file recorded by a person,
  with an outcome (pass or fail).

Status is computed on every read, never stored, so it cannot outlive the
evidence behind it:

    excepted     an approved exception is in force (it wins: that is its job)
    failing      an automated check fails, or the newest manual evidence is a fail
    not_assessed nothing has passed yet (no sources, or none could be evaluated)
    stale        the newest passing evidence is older than freshness_days
    passing      otherwise

Exceptions are four-eyes (approver ≠ requester) and time-boxed (at most
`MAX_EXCEPTION_DAYS`); an expired one stops counting without anyone acting.

Pure rules at the top (unit-tested without a DB), async fetchers below.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

STATUSES = ("passing", "failing", "stale", "excepted", "not_assessed")
RESULT_STATES = ("pass", "fail", "unknown")
MAX_EXCEPTION_DAYS = 365
MAX_FRESHNESS_DAYS = 3650
DEFAULT_FRESHNESS_DAYS = 90
SEVERITIES = ("critical", "high", "medium", "low")

#: Manual evidence uploads: size cap and the only types accepted. The stored
#: content type comes from this map, never from the client, and downloads are
#: served as attachments — an uploaded .html could otherwise run script on the
#: API origin.
MAX_EVIDENCE_BYTES = 10 * 1024 * 1024
EVIDENCE_TYPES: Dict[str, str] = {
    ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".txt": "text/plain", ".log": "text/plain",
    ".md": "text/plain", ".csv": "text/csv", ".json": "application/json",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def workspace_prefix(workspace_id: int) -> str:
    """Object-store prefix for a workspace's evidence files and packs. The
    workspace purge deletes it, so every compliance object must live under it."""
    return f"compliance/ws{int(workspace_id)}/"


class ComplianceError(ValueError):
    """A request the control model refuses (maps to 422 / 409 in the API)."""


# ---------------------------------------------------------------------------
# Evidence-source catalogue
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceDef:
    type: str
    label: str
    description: str
    defaults: Dict[str, Any] = field(default_factory=dict)


SOURCES: Dict[str, SourceDef] = {s.type: s for s in (
    SourceDef("access_review_recent", "Access review signed off within N days",
              "The newest signed-off access review covering this workspace (its own, or an "
              "instance-wide one) is no older than `days`.", {"days": 90}),
    SourceDef("audit_chain_verifies", "Audit trail hash chain verifies",
              "Every audit row is intact and linked to its predecessor (the same check as "
              "the Verify chain button)."),
    SourceDef("data_policy_on_all_projects", "Data-capture policy set on every project",
              "Each project in the workspace has an explicit data policy rather than the defaults."),
    SourceDef("no_findings_past_sla", "No open security findings past their SLA",
              "No open or in-progress finding of the chosen severities is past its due date.",
              {"severities": ["critical"]}),
    SourceDef("restore_drill_recent", "Restore drill passed within N days",
              "scripts/restore_drill.sh --report-to recorded a passing drill no older than `days`.",
              {"days": 90}),
    SourceDef("mfa_enforced_for_admins", "MFA enforced for workspace admins",
              "MFA_REQUIRED is on for the instance, or every active workspace admin has MFA enabled."),
    SourceDef("sso_enforced", "Password sign-in disabled (SSO only)",
              "The PASSWORD_LOGIN_DISABLED instance setting is on."),
    SourceDef("api_keys_expire", "Every live API key expires",
              "Each unrevoked workspace API key has an expiry no more than `max_days` after it "
              "was created.", {"max_days": 365}),
)}


def _int_param(params: Dict[str, Any], name: str, default: int, lo: int = 1,
               hi: int = MAX_FRESHNESS_DAYS) -> int:
    value = params.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ComplianceError(f"{name} must be a whole number between {lo} and {hi}")
    return value


def validate_sources(raw: Any) -> List[Dict[str, Any]]:
    """Normalise a control's evidence_sources: known types, valid params,
    defaults filled in, no duplicates."""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 20:
        raise ComplianceError("evidence_sources must be a list of at most 20 sources")
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict) or item.get("type") not in SOURCES:
            raise ComplianceError(
                f"unknown evidence source; expected one of {', '.join(SOURCES)}")
        defn = SOURCES[item["type"]]
        params = item.get("params") or {}
        if not isinstance(params, dict):
            raise ComplianceError("params must be an object")
        unknown = set(params) - set(defn.defaults)
        if unknown:
            raise ComplianceError(f"{defn.type}: unknown parameter(s) {', '.join(sorted(unknown))}")
        clean: Dict[str, Any] = {}
        if "days" in defn.defaults:
            clean["days"] = _int_param(params, "days", defn.defaults["days"])
        if "max_days" in defn.defaults:
            clean["max_days"] = _int_param(params, "max_days", defn.defaults["max_days"])
        if "severities" in defn.defaults:
            sev = params.get("severities", defn.defaults["severities"])
            if (not isinstance(sev, list) or not sev
                    or any(s not in SEVERITIES for s in sev)):
                raise ComplianceError(f"severities must be a non-empty subset of {', '.join(SEVERITIES)}")
            clean["severities"] = [s for s in SEVERITIES if s in sev]
        key = (defn.type, tuple(sorted((k, str(v)) for k, v in clean.items())))
        if key in seen:
            continue
        seen.add(key)
        out.append({"type": defn.type, "params": clean})
    return out


def validate_freshness(days: Any) -> int:
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_FRESHNESS_DAYS:
        raise ComplianceError(f"freshness_days must be between 1 and {MAX_FRESHNESS_DAYS}")
    return days


def source_label(source: Dict[str, Any]) -> str:
    defn = SOURCES.get(source.get("type"))
    if not defn:
        return str(source.get("type"))
    params = source.get("params") or {}
    label = defn.label
    if "days" in params:
        label = label.replace("N days", f"{params['days']} days")
    if "severities" in params:
        label += f" ({', '.join(params['severities'])})"
    if "max_days" in params:
        label += f" (≤ {params['max_days']} days)"
    return label


# ---------------------------------------------------------------------------
# Results and pure judges (one per source)
# ---------------------------------------------------------------------------

@dataclass
class EvidenceResult:
    type: str
    label: str
    status: str                      # pass | fail | unknown
    reason: str
    observed_at: Optional[datetime] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "label": self.label, "status": self.status,
                "reason": self.reason, "observed_at": self.observed_at}


def _days_ago(then: datetime, now: datetime) -> int:
    return max(0, (now - then).days)


def judge_recent(kind: str, last_at: Optional[datetime], days: int, now: datetime,
                 *, newer_failure_at: Optional[datetime] = None) -> Tuple[str, str, Optional[datetime]]:
    """Shared rule for "X happened within N days" sources."""
    if last_at is None:
        suffix = f"; the latest {kind} failed" if newer_failure_at else ""
        return "fail", f"no passing {kind} has been recorded{suffix}", None
    age = _days_ago(last_at, now)
    note = ""
    if newer_failure_at and newer_failure_at > last_at:
        note = f"; a later {kind} on {newer_failure_at.date().isoformat()} failed"
    if last_at < now - timedelta(days=days):
        return "fail", f"the latest passing {kind} was {age} days ago (limit {days}){note}", last_at
    return "pass", f"passing {kind} {age} days ago (limit {days}){note}", last_at


def judge_access_review(last_signed_off_at: Optional[datetime], days: int, now: datetime):
    if last_signed_off_at is None:
        return "fail", "no access review has been signed off for this workspace", None
    status, reason, at = judge_recent("access review", last_signed_off_at, days, now)
    return status, reason.replace("passing access review", "access review signed off"), at


def judge_restore_drill(last_pass_at: Optional[datetime], last_any: Optional[Tuple[datetime, str]],
                        days: int, now: datetime):
    newer_fail = last_any[0] if last_any and last_any[1] == "fail" else None
    if last_pass_at is None and last_any is None:
        return "fail", "no restore drill has been reported (scripts/restore_drill.sh --report-to)", None
    return judge_recent("restore drill", last_pass_at, days, now, newer_failure_at=newer_fail)


def judge_chain(verdict: Optional[Dict[str, Any]], now: datetime):
    if verdict is None:
        return "unknown", "the audit chain could not be checked", None
    if verdict.get("verified"):
        return "pass", f"{verdict.get('rows_checked', 0)} audit rows verified", now
    where = "in this workspace" if verdict.get("in_this_workspace") else "outside this workspace"
    if verdict.get("unverifiable"):
        return "fail", f"rows predate hash chaining (first at a row {where}); the trail cannot be verified", now
    return "fail", f"the chain breaks at a row {where}; later rows are unverifiable", now


def judge_data_policies(projects: Sequence[Tuple[str, Any]], now: datetime):
    """projects: (name, stored data_policy)."""
    if not projects:
        return "unknown", "the workspace has no projects", None
    missing = [name for name, stored in projects if not (isinstance(stored, dict) and stored)]
    if missing:
        shown = ", ".join(missing[:5]) + (f" and {len(missing) - 5} more" if len(missing) > 5 else "")
        return "fail", f"{len(missing)} of {len(projects)} project(s) use the default policy: {shown}", now
    return "pass", f"all {len(projects)} project(s) have an explicit data policy", now


def judge_overdue_findings(overdue: Dict[str, int], severities: Sequence[str], now: datetime):
    total = sum(overdue.get(s, 0) for s in severities)
    if total:
        parts = ", ".join(f"{overdue[s]} {s}" for s in severities if overdue.get(s))
        return "fail", f"{total} finding(s) past their SLA: {parts}", now
    return "pass", f"no {'/'.join(severities)} finding is past its SLA", now


def judge_mfa(mfa_required: Optional[bool], admins: Sequence[Tuple[str, bool]], now: datetime):
    """admins: (email, mfa_enabled) of active workspace admins."""
    if mfa_required:
        return "pass", "MFA_REQUIRED is on for the instance", now
    if not admins:
        return "unknown", "the workspace has no active admins", None
    without = [email for email, mfa in admins if not mfa]
    if without:
        return "fail", (f"{len(without)} of {len(admins)} admin(s) have no MFA and MFA_REQUIRED is off"), now
    return "pass", f"all {len(admins)} admin(s) have MFA enabled", now


def judge_sso(password_login_disabled: Optional[bool], now: datetime):
    if password_login_disabled is None:
        return "unknown", "the instance setting could not be read", None
    if password_login_disabled:
        return "pass", "password sign-in is disabled; people sign in through SSO", now
    return "fail", "password sign-in is enabled (PASSWORD_LOGIN_DISABLED is off)", now


def judge_api_keys(keys: Sequence[Tuple[datetime, Optional[datetime]]], max_days: int, now: datetime):
    """keys: (created_at, expires_at) of live keys."""
    if not keys:
        return "pass", "no live API keys", now
    bad = sum(1 for created, expires in keys
              if expires is None or expires > created + timedelta(days=max_days))
    if bad:
        return "fail", f"{bad} of {len(keys)} live key(s) never expire or last over {max_days} days", now
    return "pass", f"all {len(keys)} live key(s) expire within {max_days} days", now


# ---------------------------------------------------------------------------
# Status and exceptions (pure)
# ---------------------------------------------------------------------------

def _f(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def exception_state(exc: Any, now: datetime) -> str:
    status, expires = _f(exc, "status"), _f(exc, "expires_at")
    if status == "approved" and expires is not None and expires <= now:
        return "expired"
    return status


def exception_active(exc: Any, now: datetime) -> bool:
    return exception_state(exc, now) == "approved"


def validate_exception_request(reason: Optional[str], expires_at: Optional[datetime],
                               now: datetime) -> None:
    if not (reason or "").strip():
        raise ComplianceError("an exception needs a reason")
    if expires_at is None:
        raise ComplianceError("an exception needs an expiry date")
    if expires_at <= now:
        raise ComplianceError("the expiry must be in the future")
    if expires_at > now + timedelta(days=MAX_EXCEPTION_DAYS):
        raise ComplianceError(
            f"an exception can last at most {MAX_EXCEPTION_DAYS} days; request a new one at expiry")


def check_approval(exc: Any, approver_id: int, now: datetime) -> None:
    """Four-eyes and expiry rules for approving an exception."""
    if exc.status != "pending":
        raise ComplianceError(f"this exception is {exc.status}, not pending")
    if exc.requested_by_id == approver_id:
        raise ComplianceError("four-eyes rule: the requester cannot approve their own exception")
    if exc.expires_at <= now:
        raise ComplianceError("this exception's expiry has already passed; request a new one")
    if exc.expires_at > now + timedelta(days=MAX_EXCEPTION_DAYS):
        raise ComplianceError(f"an exception can last at most {MAX_EXCEPTION_DAYS} days")


def control_status(*, results: Sequence[EvidenceResult], manual: Sequence[Tuple[datetime, str]],
                   exceptions: Sequence[Any], freshness_days: int,
                   now: datetime) -> Dict[str, Any]:
    """The control's status. `manual`: (created_at, outcome) pairs."""
    passes = [r.observed_at for r in results if r.status == "pass" and r.observed_at]
    passes += [at for at, outcome in manual if outcome == "pass"]
    newest_pass = max(passes) if passes else None
    base = {"newest_pass_at": newest_pass}

    active = [e for e in exceptions if exception_active(e, now)]
    if active:
        until = max(_f(e, "expires_at") for e in active)
        return {**base, "status": "excepted",
                "reason": f"an approved exception is in force until {until.date().isoformat()}"}

    failing = [r for r in results if r.status == "fail"]
    if failing:
        return {**base, "status": "failing", "reason": f"{failing[0].label}: {failing[0].reason}"}
    latest_manual = max(manual, key=lambda m: m[0]) if manual else None
    if latest_manual and latest_manual[1] == "fail":
        return {**base, "status": "failing", "reason": "the newest manual evidence records a failure"}

    if newest_pass is None:
        unknown = [r for r in results if r.status == "unknown"]
        reason = (f"{unknown[0].label}: {unknown[0].reason}" if unknown
                  else "no evidence has been recorded yet")
        return {**base, "status": "not_assessed", "reason": reason}
    if newest_pass < now - timedelta(days=freshness_days):
        return {**base, "status": "stale",
                "reason": (f"the newest passing evidence is {_days_ago(newest_pass, now)} days old "
                           f"(freshness SLA {freshness_days} days)")}
    return {**base, "status": "passing", "reason": "evidence is current and passing"}


def evidence_file_type(filename: str) -> str:
    """Content type for an allowed upload, or ComplianceError."""
    ext = os.path.splitext((filename or "").lower())[1]
    if ext not in EVIDENCE_TYPES:
        raise ComplianceError(
            f"file type not accepted; allowed: {', '.join(sorted(EVIDENCE_TYPES))}")
    return EVIDENCE_TYPES[ext]


def safe_filename(filename: str) -> str:
    base = os.path.basename((filename or "").replace("\\", "/")) or "evidence"
    clean = "".join(c if c.isalnum() or c in "._- " else "_" for c in base).strip()
    return (clean or "evidence")[:120]


# ---------------------------------------------------------------------------
# SOC 2 starter set (docs/SOC2_CONTROLS.md as editable data)
# ---------------------------------------------------------------------------

def _src(kind: str, **params: Any) -> Dict[str, Any]:
    return {"type": kind, "params": params}


SOC2_STARTER: List[Dict[str, Any]] = [
    {"key": "cc6.1-accounts", "reference": "CC6.1", "title": "Unique, authenticated accounts",
     "description": "JWT sessions with refresh-token rotation and family revocation; API keys "
                    "hashed at rest with prefix-only display. Evidence: auth audit rows.",
     "sources": []},
    {"key": "cc6.1-mfa", "reference": "CC6.1", "title": "Multi-factor authentication for admins",
     "description": "TOTP + recovery codes; MFA_REQUIRED forces enrolment before a session is "
                    "issued (docs/ENTERPRISE_AUTH.md).",
     "sources": [_src("mfa_enforced_for_admins")]},
    {"key": "cc6.1-sso", "reference": "CC6.1", "title": "Single sign-on enforced",
     "description": "OIDC, SAML 2.0 or LDAP with JIT provisioning; PASSWORD_LOGIN_DISABLED makes "
                    "SSO the only way in (instance admins keep break-glass).",
     "sources": [_src("sso_enforced")]},
    {"key": "cc6.2-provisioning", "reference": "CC6.2", "title": "Provisioning and deprovisioning",
     "description": "SCIM 2.0 provisioning; deactivation revokes live refresh tokens; is_active "
                    "checked on every request. Reviewed in the periodic access review.",
     "sources": [_src("access_review_recent", days=90)]},
    {"key": "cc6.3-access-review", "reference": "CC6.3", "title": "Role-based access, reviewed quarterly",
     "description": "Workspace/team/project RBAC; a quarterly access review (Access reviews page) "
                    "is signed off with every keep/revoke decision in the audit trail.",
     "sources": [_src("access_review_recent", days=90)]},
    {"key": "cc6.3-machine-access", "reference": "CC6.3", "title": "Least privilege for machine access",
     "description": "API keys scoped to a workspace and optionally a project, with an expiry.",
     "sources": [_src("api_keys_expire", max_days=365)]},
    {"key": "cc6.1-secrets", "reference": "CC6.1", "title": "Secrets encrypted at rest",
     "description": "Fernet keyring (SECRETS_KEY, SECRETS_KEY_PREVIOUS), scripts/rotate_secrets.py.",
     "sources": []},
    {"key": "cc6.6-network", "reference": "CC6.6", "title": "Outbound network control",
     "description": "net_guard validates every user-supplied URL; private-network targets off by default.",
     "sources": []},
    {"key": "cc6.7-transport", "reference": "CC6.7", "title": "Transport security",
     "description": "REQUIRE_TRANSPORT_SECURITY refuses to boot on plaintext Postgres/Redis/S3.",
     "sources": []},
    {"key": "cc7.2-audit-trail", "reference": "CC7.2", "title": "Tamper-evident audit trail",
     "description": "Append-only trigger plus hash chain; the chain is verified on demand.",
     "sources": [_src("audit_chain_verifies")]},
    {"key": "cc7.1-vulnerabilities", "reference": "CC7.1", "title": "Vulnerability management",
     "description": "Security findings have owners and SLA due dates; nothing critical or high "
                    "stays open past its SLA without an accepted risk.",
     "sources": [_src("no_findings_past_sla", severities=["critical", "high"])]},
    {"key": "cc7.2-monitoring", "reference": "CC7.2", "title": "Security monitoring",
     "description": "Prometheus metrics and alert rules, structured logs, SIEM streaming.",
     "sources": []},
    {"key": "c1.1-data-capture", "reference": "C1.1", "title": "Data capture minimisation",
     "description": "Per-project data policy under the instance MAX_CAPTURE_LEVEL ceiling; redaction "
                    "at capture and ingestion.",
     "sources": [_src("data_policy_on_all_projects")]},
    {"key": "cc8.1-change", "reference": "CC8.1", "title": "Reviewed, tested changes",
     "description": "CI unit/integration tests, migration round-trip; branch protection is yours.",
     "sources": []},
    {"key": "cc8.1-agent-changes", "reference": "CC8.1", "title": "Agent-made changes reviewed",
     "description": "Case proposals queue with separation of duties; auto-apply can be switched off.",
     "sources": []},
    {"key": "a1.2-restore", "reference": "A1.2", "title": "Backups restore, tested quarterly",
     "description": "scripts/restore_drill.sh restores the latest dump into a throwaway database and "
                    "checks schema, data, audit chain and artifacts; --report-to records the result.",
     "sources": [_src("restore_drill_recent", days=90)]},
    {"key": "p4.3-deletion", "reference": "P4.3", "title": "Data deletion and right to erasure",
     "description": "Workspace purge covers every workspace table and object prefix; per-project "
                    "retention; DELETE /api/auth/me erases personal data.",
     "sources": []},
]


# ---------------------------------------------------------------------------
# Data fetchers (async session)
# ---------------------------------------------------------------------------

_CHAIN_CACHE: Dict[str, Any] = {}
_CHAIN_TTL_SECONDS = 300


async def verify_audit_chain(session, workspace_id: Optional[int] = None,
                             batch: int = 2000) -> Dict[str, Any]:
    """Walk the whole (global) audit chain in id batches — bounded memory,
    unlike loading the table. Cached for a few minutes per chain head, since
    the controls page evaluates this for every control that uses it."""
    from sqlalchemy import func
    from sqlmodel import select
    from app.models import AuditLog
    from app.services.audit import ROOT_HASH, chain_hash

    head = (await session.exec(select(func.max(AuditLog.id), func.count(AuditLog.id)))).one()
    cache_key = f"{head[0]}:{head[1]}"
    cached = _CHAIN_CACHE.get("result")
    if cached and _CHAIN_CACHE.get("key") == cache_key \
            and time.monotonic() - _CHAIN_CACHE.get("at", 0) < _CHAIN_TTL_SECONDS:
        verdict = dict(cached)
    else:
        cols = (AuditLog.id, AuditLog.entity_type, AuditLog.entity_id, AuditLog.action,
                AuditLog.user_id, AuditLog.workspace_id, AuditLog.timestamp, AuditLog.changes,
                AuditLog.prev_hash, AuditLog.row_hash)
        expected = ROOT_HASH
        checked = 0
        last_id = 0
        verdict = {"verified": True}
        while True:
            rows = (await session.exec(select(*cols).where(AuditLog.id > last_id)
                                       .order_by(AuditLog.id).limit(batch))).all()
            if not rows:
                break
            for r in rows:
                (rid, etype, eid, action, uid, wsid, ts, changes, prev, stored) = r
                bad = (not stored or (prev or ROOT_HASH) != expected or chain_hash(
                    entity_type=etype, entity_id=eid, action=action, user_id=uid,
                    workspace_id=wsid, timestamp=ts, changes=changes, prev_hash=prev) != stored)
                if bad:
                    verdict = {"verified": False, "unverifiable": not stored,
                               "first_bad_row_id": rid, "first_bad_row_timestamp": ts,
                               "first_bad_workspace_id": wsid}
                    break
                expected = stored
                checked += 1
                last_id = rid
            if not verdict["verified"]:
                break
        verdict["rows_checked"] = checked
        _CHAIN_CACHE.update(key=cache_key, result=dict(verdict), at=time.monotonic())

    # Never tell one workspace which other workspace (or row) broke the chain.
    bad_ws = verdict.pop("first_bad_workspace_id", None)
    if not verdict["verified"]:
        verdict["in_this_workspace"] = workspace_id is not None and bad_ws == workspace_id
        if not verdict["in_this_workspace"]:
            verdict.pop("first_bad_row_id", None)
            verdict.pop("first_bad_row_timestamp", None)
    return verdict


async def _last_access_review(session, workspace_id: int) -> Optional[datetime]:
    from sqlalchemy import func, or_
    from sqlmodel import select
    from app.models import AccessReviewCampaign
    return (await session.exec(select(func.max(AccessReviewCampaign.signed_off_at)).where(
        AccessReviewCampaign.status == "signed_off",
        or_(AccessReviewCampaign.workspace_id == workspace_id,
            AccessReviewCampaign.workspace_id == None)))).one()  # noqa: E711


async def _drills(session) -> Tuple[Optional[datetime], Optional[Tuple[datetime, str]]]:
    from sqlalchemy import func
    from sqlmodel import select
    from app.models import DrillResult
    last_pass = (await session.exec(select(func.max(DrillResult.ran_at)).where(
        DrillResult.status == "pass"))).one()
    last = (await session.exec(select(DrillResult).order_by(DrillResult.ran_at.desc()).limit(1))).first()
    return last_pass, ((last.ran_at, last.status) if last else None)


async def _projects(session, workspace_id: int):
    from sqlmodel import select
    from app.models import Project
    return [(p.name, p.data_policy) for p in (await session.exec(
        select(Project).where(Project.workspace_id == workspace_id).order_by(Project.name))).all()]


async def overdue_findings(session, workspace_id: int, now: datetime) -> Dict[str, int]:
    from sqlalchemy import func
    from sqlmodel import select
    from app.models import Project, SecurityFinding
    from app.services.finding_lifecycle import ACTIONABLE
    rows = (await session.exec(
        select(SecurityFinding.severity, func.count(SecurityFinding.id))
        .join(Project, Project.id == SecurityFinding.project_id)
        .where(Project.workspace_id == workspace_id,
               SecurityFinding.superseded_by_id == None,  # noqa: E711
               SecurityFinding.status.in_(list(ACTIONABLE)),
               SecurityFinding.due_at != None,  # noqa: E711
               SecurityFinding.due_at < now)
        .group_by(SecurityFinding.severity))).all()
    return {(sev or "").lower(): n for sev, n in rows}


async def workspace_admins(session, workspace_id: int) -> List[Any]:
    from sqlmodel import select
    from app.models import Role, User, UserWorkspace
    roles = {r.id: r.name for r in (await session.exec(select(Role))).all()}
    rows = (await session.exec(select(UserWorkspace, User).join(User, User.id == UserWorkspace.user_id)
                               .where(UserWorkspace.workspace_id == workspace_id))).all()
    out = []
    for uw, user in rows:
        role = roles.get(uw.role_id) if uw.role_id else None
        if (role == "Workspace Admin" or (role is None and (uw.role or "").lower() in ("admin", "owner"))) \
                and user.is_active:
            out.append(user)
    return out


async def _live_keys(session, workspace_id: int):
    from sqlmodel import select
    from app.models import ApiKey
    return [(k.created_at, k.expires_at) for k in (await session.exec(select(ApiKey).where(
        ApiKey.workspace_id == workspace_id, ApiKey.revoked_at == None))).all()]  # noqa: E711


def _setting(key: str) -> Optional[bool]:
    try:
        from app.services.instance_settings import effective
        return bool(effective(key))
    except Exception:  # noqa: BLE001 — an unreadable setting is "unknown", not a crash
        return None


class Evaluator:
    """Evaluates sources for one workspace, memoising each underlying fetch so
    a page of controls sharing a source costs one query, not one per control."""

    def __init__(self, session, workspace_id: int, now: Optional[datetime] = None):
        self.session = session
        self.workspace_id = workspace_id
        self.now = now or datetime.utcnow()
        self._memo: Dict[str, Any] = {}

    async def _get(self, key: str, fn):
        if key not in self._memo:
            self._memo[key] = await fn()
        return self._memo[key]

    async def evaluate(self, source: Dict[str, Any]) -> EvidenceResult:
        kind = source.get("type")
        params = source.get("params") or {}
        label = source_label(source)
        now, s, ws = self.now, self.session, self.workspace_id
        try:
            if kind == "access_review_recent":
                last = await self._get("access_review", lambda: _last_access_review(s, ws))
                verdict = judge_access_review(last, params.get("days", 90), now)
            elif kind == "audit_chain_verifies":
                chain = await self._get("chain", lambda: verify_audit_chain(s, ws))
                verdict = judge_chain(chain, now)
            elif kind == "data_policy_on_all_projects":
                verdict = judge_data_policies(await self._get("projects", lambda: _projects(s, ws)), now)
            elif kind == "no_findings_past_sla":
                overdue = await self._get("overdue", lambda: overdue_findings(s, ws, now))
                verdict = judge_overdue_findings(overdue, params.get("severities", ["critical"]), now)
            elif kind == "restore_drill_recent":
                last_pass, last_any = await self._get("drills", lambda: _drills(s))
                verdict = judge_restore_drill(last_pass, last_any, params.get("days", 90), now)
            elif kind == "mfa_enforced_for_admins":
                admins = await self._get("admins", lambda: workspace_admins(s, ws))
                verdict = judge_mfa(_setting("MFA_REQUIRED"),
                                    [(u.email, bool(u.mfa_enabled)) for u in admins], now)
            elif kind == "sso_enforced":
                verdict = judge_sso(_setting("PASSWORD_LOGIN_DISABLED"), now)
            elif kind == "api_keys_expire":
                keys = await self._get("keys", lambda: _live_keys(s, ws))
                verdict = judge_api_keys(keys, params.get("max_days", 365), now)
            else:
                verdict = ("unknown", "this evidence source is no longer supported", None)
        except Exception as exc:  # noqa: BLE001 — one broken source must not hide the rest
            verdict = ("unknown", f"could not be evaluated ({type(exc).__name__})", None)
        status, reason, observed = verdict
        return EvidenceResult(kind, label, status, reason, observed)

    async def evaluate_control(self, control: Any, manual: Iterable[Tuple[datetime, str]],
                               exceptions: Sequence[Any]) -> Dict[str, Any]:
        results = [await self.evaluate(src) for src in (control.evidence_sources or [])]
        status = control_status(results=results, manual=list(manual), exceptions=exceptions,
                                freshness_days=control.freshness_days or DEFAULT_FRESHNESS_DAYS,
                                now=self.now)
        return {**status, "results": [r.as_dict() for r in results]}


def drill_view(drill: Any, *, full: bool) -> Dict[str, Any]:
    """A drill result as shown outside the instance-admin screen.

    Drill check details carry instance-wide facts (row counts across every
    tenant, host names), so workspace views get the check names and verdicts
    only; instance admins get the details.
    """
    checks = []
    for c in drill.checks or []:
        item = {"check": c.get("check"), "status": c.get("status")}
        if full:
            item["detail"] = c.get("detail")
        checks.append(item)
    out = {"id": drill.id, "ran_at": drill.ran_at, "status": drill.status, "checks": checks,
           "duration_seconds": drill.duration_seconds}
    if full:
        out.update(dump_name=drill.dump_name, target=drill.target,
                   reporter=drill.reporter_label, received_at=drill.received_at)
    return out


# ---------------------------------------------------------------------------
# Read model shared by the API and the evidence pack
# ---------------------------------------------------------------------------

def exception_view(exc: Any, people: Dict[int, str], now: datetime) -> Dict[str, Any]:
    return {"id": exc.id, "control_id": exc.control_id, "reason": exc.reason,
            "compensating_control": exc.compensating_control,
            "status": exception_state(exc, now), "requested_by": people.get(exc.requested_by_id),
            "requested_by_id": exc.requested_by_id, "requested_at": exc.requested_at,
            "approved_by": people.get(exc.approved_by_id), "approved_at": exc.approved_at,
            "decided_note": exc.decided_note, "expires_at": exc.expires_at}


def evidence_view(ev: Any, people: Dict[int, str]) -> Dict[str, Any]:
    return {"id": ev.id, "control_id": ev.control_id, "outcome": ev.outcome, "note": ev.note,
            "file_name": ev.file_name, "content_type": ev.content_type,
            "size_bytes": ev.size_bytes, "sha256": ev.sha256, "has_file": bool(ev.file_key),
            "created_by": people.get(ev.created_by_id), "created_at": ev.created_at}


async def people_by_id(session, ids: Iterable[Optional[int]]) -> Dict[int, str]:
    from sqlmodel import select
    from app.models import User
    wanted = sorted({i for i in ids if i})
    if not wanted:
        return {}
    return {u.id: u.email for u in (await session.exec(select(User).where(User.id.in_(wanted)))).all()}


async def controls_overview(session, workspace_id: int, *, now: Optional[datetime] = None,
                            control_ids: Optional[Sequence[int]] = None,
                            with_detail: bool = False) -> List[Dict[str, Any]]:
    """Every control of the workspace with its computed status."""
    from sqlmodel import select
    from app.models import ComplianceControl, ControlEvidence, ControlException
    now = now or datetime.utcnow()
    q = select(ComplianceControl).where(ComplianceControl.workspace_id == workspace_id)
    if control_ids is not None:
        q = q.where(ComplianceControl.id.in_(list(control_ids) or [-1]))
    controls = (await session.exec(q.order_by(ComplianceControl.framework,
                                              ComplianceControl.reference,
                                              ComplianceControl.id))).all()
    ids = [c.id for c in controls] or [-1]
    evidence = (await session.exec(select(ControlEvidence).where(
        ControlEvidence.workspace_id == workspace_id, ControlEvidence.control_id.in_(ids))
        .order_by(ControlEvidence.created_at.desc()))).all()
    exceptions = (await session.exec(select(ControlException).where(
        ControlException.workspace_id == workspace_id, ControlException.control_id.in_(ids))
        .order_by(ControlException.requested_at.desc()))).all()
    people = await people_by_id(session, [c.owner_id for c in controls]
                                + [e.created_by_id for e in evidence]
                                + [x.requested_by_id for x in exceptions]
                                + [x.approved_by_id for x in exceptions])
    ev_by: Dict[int, List[Any]] = {}
    for e in evidence:
        ev_by.setdefault(e.control_id, []).append(e)
    ex_by: Dict[int, List[Any]] = {}
    for x in exceptions:
        ex_by.setdefault(x.control_id, []).append(x)

    evaluator = Evaluator(session, workspace_id, now)
    out = []
    for c in controls:
        evs, exs = ev_by.get(c.id, []), ex_by.get(c.id, [])
        evaluated = await evaluator.evaluate_control(
            c, [(e.created_at, e.outcome) for e in evs], exs)
        row = {
            "id": c.id, "workspace_id": c.workspace_id, "framework": c.framework,
            "reference": c.reference, "title": c.title, "description": c.description,
            "owner_id": c.owner_id, "owner": people.get(c.owner_id),
            "evidence_sources": c.evidence_sources or [], "freshness_days": c.freshness_days,
            "seeded": bool(c.seed_key), "created_at": c.created_at, "updated_at": c.updated_at,
            "status": evaluated["status"], "status_reason": evaluated["reason"],
            "newest_pass_at": evaluated["newest_pass_at"], "results": evaluated["results"],
            "manual_evidence_count": len(evs),
            "active_exception": next((exception_view(x, people, now) for x in exs
                                      if exception_active(x, now)), None),
            "pending_exceptions": sum(1 for x in exs if x.status == "pending"),
        }
        if with_detail:
            row["evidence"] = [evidence_view(e, people) for e in evs]
            row["exceptions"] = [exception_view(x, people, now) for x in exs]
        out.append(row)
    return out


def status_counts(rows: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts = {s: 0 for s in STATUSES}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return counts
