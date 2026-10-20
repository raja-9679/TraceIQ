"""Read-only tools for the in-app assistant (WP14a, E20, decision W1).

The model never touches the database. It names a tool from `TOOLS`; this
module validates the arguments, runs a fixed query and hands back a small,
redacted JSON payload. Three layers keep the assistant inside what the caller
could see through the UI/API:

1. **Scope.** `ToolContext.allowed_project_ids` is computed once per question
   with the same rule as the project pages (`app.api.reports.accessible_projects`:
   project access per project, API keys narrowed to their project). A project
   id the model names is re-checked against it; an id outside it gets the same
   answer as a missing one ("not accessible"), so the tool is no oracle.
2. **Queries.** `AssistantData` filters every query by those project ids, and a
   record fetched by id (run, cluster) is refused unless its project is in scope.
3. **Post-filter.** `run_tool` drops any item whose `project_id` is outside the
   scope before anything is serialised — so a data-layer bug (or a fake one in
   tests) still cannot widen what reaches the model.

There are no write paths, no run dispatch and no artifact access here: the
tools return text fields only (never trace/video/HAR URLs, headers or bodies).

Every item carries a stable `ref` (`run:12`, `result:34`, `cluster:5`,
`finding:9`, `case:7`, `project:3`, `readiness:3`). The model must cite refs;
`app/services/assistant.py` drops any ref no tool returned in the conversation.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, FrozenSet, List, Optional, Sequence

from sqlalchemy import or_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.redaction import (
    RedactionPolicy, _URL_RE, _redact_url, redact_text,
)

# --------------------------------------------------------------------------
# Refs
# --------------------------------------------------------------------------

REF_KINDS = ("project", "run", "result", "cluster", "finding", "case", "readiness")
REF_RE = re.compile(r"^(%s):(\d{1,12})$" % "|".join(REF_KINDS))


def make_ref(kind: str, record_id: int) -> str:
    return f"{kind}:{int(record_id)}"


# --------------------------------------------------------------------------
# Redaction for anything headed to the LLM
# --------------------------------------------------------------------------

# Value patterns from redaction.py minus "phone": its digit-run rule eats ISO
# dates ("2026-10-16 22"), which the assistant needs to reason about time.
_LLM_POLICY = RedactionPolicy(patterns=("cloud_keys", "jwt", "aadhaar", "pan", "email"))
_EXTRA_SECRETS = (
    # TraceIQ API keys, bearer/basic credentials, key=value secrets.
    (re.compile(r"\btiq_[A-Za-z0-9_-]{8,}"), "[REDACTED:api_key]"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [REDACTED:token]"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
                r"client[_-]?secret|session(?:id)?|cookie|authorization)"
                r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&\"']+)"),
     r"\1\2[REDACTED]"),
)


def redact_for_llm(text: Any) -> Any:
    """Redact one string for an LLM prompt: the shared value patterns (cloud
    keys, JWTs, card/Aadhaar numbers, emails), credential-looking key=value
    pairs and bearer tokens, and every URL's query string, fragment and
    opaque path tokens (reset links, signed URLs)."""
    if not isinstance(text, str) or not text:
        return text
    out = redact_text(text, _LLM_POLICY) or ""
    for pattern, repl in _EXTRA_SECRETS:
        out = pattern.sub(repl, out)
    return _URL_RE.sub(_redact_url, out)


def redact_tree(value: Any) -> Any:
    """`redact_for_llm` over every string (keys included) in a JSON value."""
    if isinstance(value, str):
        return redact_for_llm(value)
    if isinstance(value, dict):
        return {redact_for_llm(str(k)): redact_tree(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_tree(v) for v in value]
    return value


# --------------------------------------------------------------------------
# Context, results, errors
# --------------------------------------------------------------------------

MAX_ITEMS = 25
MAX_TEXT = 400          # chars per free-text field (errors, titles)
MAX_RESULT_CHARS = 6000  # serialised size of one tool result sent to the model


class ToolError(Exception):
    """A refusal or bad argument, reported back to the model as the result."""


@dataclass
class ToolContext:
    session: Any
    workspace_id: int
    allowed_project_ids: FrozenSet[int]
    project_names: Dict[int, str]
    focus_project_id: Optional[int] = None
    data: Any = None
    now: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ToolResult:
    tool: str
    args: Dict[str, Any]
    payload: Dict[str, Any] = field(default_factory=dict)   # what the model sees
    refs: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # ref -> meta
    project_ids: List[int] = field(default_factory=list)    # projects whose data was read
    error: Optional[str] = None
    truncated: bool = False

    def audit(self) -> Dict[str, Any]:
        """What is stored for the audit trail: no payload, only what was asked
        and which records came back."""
        out: Dict[str, Any] = {"tool": self.tool, "args": self.args,
                               "refs": list(self.refs)[:60], "items": len(self.refs)}
        if self.error:
            out["error"] = self.error
        if self.truncated:
            out["truncated"] = True
        return out


def _clip(value: Any, n: int = MAX_TEXT) -> Optional[str]:
    if value is None:
        return None
    s = str(value)
    return s if len(s) <= n else s[: n - 1] + "…"


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.replace(microsecond=0).isoformat() + "Z" if dt else None


def _status(v: Any) -> str:
    return str(getattr(v, "value", v))


# --------------------------------------------------------------------------
# Argument validation
# --------------------------------------------------------------------------


def _int_arg(args: Dict[str, Any], name: str, *, default: Optional[int] = None,
             lo: int = 1, hi: int = 10 ** 12) -> Optional[int]:
    raw = args.get(name, default)
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise ToolError(f"{name} must be a whole number")
    if isinstance(raw, str) and raw.strip().isdigit():
        raw = int(raw.strip())
    if not isinstance(raw, int):
        raise ToolError(f"{name} must be a whole number")
    return max(lo, min(hi, raw))


def _str_arg(args: Dict[str, Any], name: str, *, max_len: int = 100) -> Optional[str]:
    raw = args.get(name)
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        raise ToolError(f"{name} must be text")
    return raw.strip()[:max_len] or None


def _choice_arg(args: Dict[str, Any], name: str, allowed: Sequence[str],
                default: Optional[str] = None) -> Optional[str]:
    raw = args.get(name, default)
    if raw is None:
        return None
    if raw not in allowed:
        raise ToolError(f"{name} must be one of {', '.join(allowed)}")
    return raw


NOT_ACCESSIBLE = "not found or not accessible to this user"


def _scope(ctx: ToolContext, args: Dict[str, Any], *, required: bool = False) -> List[int]:
    """Project ids a tool may read: the named one (re-checked) or, without
    one, the focus project, else every project in scope."""
    pid = _int_arg(args, "project_id")
    if pid is not None:
        if pid not in ctx.allowed_project_ids:
            raise ToolError(f"project {pid} {NOT_ACCESSIBLE}")
        return [pid]
    if ctx.focus_project_id in ctx.allowed_project_ids:
        return [ctx.focus_project_id]  # type: ignore[list-item]
    if required:
        raise ToolError("project_id is required (use list_projects)")
    return sorted(ctx.allowed_project_ids)


def _check_record(ctx: ToolContext, record: Any, kind: str, record_id: int) -> None:
    if record is None or getattr(record, "project_id", None) not in ctx.allowed_project_ids:
        raise ToolError(f"{kind} {record_id} {NOT_ACCESSIBLE}")


# --------------------------------------------------------------------------
# Data layer (the only place with SQL). Every method filters by project ids.
# --------------------------------------------------------------------------


class AssistantData:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def runs(self, project_ids: Sequence[int], *, status: Optional[str],
                   since: datetime, suite_contains: Optional[str],
                   branch: Optional[str], limit: int) -> List[Any]:
        from app.models import TestRun
        from app.services.run_scope import counted
        # Validation runs (WP16c) ran an unaccepted change; the assistant's
        # "what failed?" must describe the real suite.
        stmt = select(TestRun).where(TestRun.project_id.in_(list(project_ids)),
                                     TestRun.created_at >= since, counted())
        if status:
            stmt = stmt.where(TestRun.status == status)
        if suite_contains:
            stmt = stmt.where(or_(TestRun.suite_name.ilike(f"%{suite_contains}%"),
                                  TestRun.test_case_name.ilike(f"%{suite_contains}%")))
        if branch:
            stmt = stmt.where(TestRun.git_branch == branch)
        return list((await self.session.exec(
            stmt.order_by(TestRun.created_at.desc()).limit(limit))).all())

    async def run(self, run_id: int) -> Any:
        from app.models import TestRun
        return await self.session.get(TestRun, run_id)

    async def run_results(self, run_id: int, *, failing_only: bool, limit: int) -> List[Any]:
        from app.models import TestCaseResult, TestStatus
        stmt = select(TestCaseResult).where(TestCaseResult.test_run_id == run_id)
        if failing_only:
            stmt = stmt.where(TestCaseResult.status.in_((TestStatus.FAILED, TestStatus.ERROR)))
        return list((await self.session.exec(stmt.order_by(TestCaseResult.id).limit(limit))).all())

    async def results(self, project_ids: Sequence[int], *, name_contains: Optional[str],
                      status: str, since: datetime, limit: int) -> List[tuple]:
        """(TestCaseResult, TestRun) newest first."""
        from app.models import TestCaseResult, TestRun, TestStatus
        from app.services.run_scope import counted
        stmt = (select(TestCaseResult, TestRun)
                .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
                .where(TestRun.project_id.in_(list(project_ids)), TestRun.created_at >= since,
                       counted()))
        if status == "failed":
            stmt = stmt.where(TestCaseResult.status.in_((TestStatus.FAILED, TestStatus.ERROR)))
        elif status == "passed":
            stmt = stmt.where(TestCaseResult.status == TestStatus.PASSED)
        if name_contains:
            stmt = stmt.where(TestCaseResult.test_name.ilike(f"%{name_contains}%"))
        return list((await self.session.exec(
            stmt.order_by(TestRun.created_at.desc(), TestCaseResult.id).limit(limit))).all())

    async def flaky(self, project_ids: Sequence[int], *, limit: int) -> List[tuple]:
        """(FlakeRecord, TestCase) for flaky or quarantined cases, worst first."""
        from app.models import FlakeRecord, TestCase
        from app.services.flake_policy import QUARANTINE_SCORE, active_quarantine_clause
        return list((await self.session.exec(
            select(FlakeRecord, TestCase)
            .join(TestCase, TestCase.id == FlakeRecord.test_case_id)
            .where(TestCase.project_id.in_(list(project_ids)),
                   or_(active_quarantine_clause(), FlakeRecord.flake_score >= QUARANTINE_SCORE))
            .order_by(FlakeRecord.flake_score.desc(), FlakeRecord.id).limit(limit))).all())

    async def clusters(self, project_ids: Sequence[int], *, status: str, limit: int) -> List[Any]:
        from app.models import FailureCluster
        stmt = select(FailureCluster).where(FailureCluster.project_id.in_(list(project_ids)))
        if status == "open":
            stmt = stmt.where(FailureCluster.status.in_(("open", "investigating")))
        elif status != "any":
            stmt = stmt.where(FailureCluster.status == status)
        return list((await self.session.exec(
            stmt.order_by(FailureCluster.last_seen_at.desc()).limit(limit))).all())

    async def cluster(self, cluster_id: int) -> Any:
        from app.models import FailureCluster
        return await self.session.get(FailureCluster, cluster_id)

    async def cluster_results(self, cluster_id: int, project_id: int, *, limit: int) -> List[tuple]:
        from app.models import TestCaseResult, TestRun
        return list((await self.session.exec(
            select(TestCaseResult, TestRun)
            .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
            .where(TestCaseResult.cluster_id == cluster_id, TestRun.project_id == project_id)
            .order_by(TestRun.created_at.desc()).limit(limit))).all())

    async def findings(self, project_ids: Sequence[int], *, severity: Optional[str],
                       status: str, limit: int) -> List[Any]:
        from app.models import SecurityFinding
        stmt = select(SecurityFinding).where(SecurityFinding.project_id.in_(list(project_ids)))
        from app.services.finding_lifecycle import ACTIONABLE
        if status == "open":
            stmt = stmt.where(SecurityFinding.status.in_(sorted(ACTIONABLE)))
        elif status == "closed":
            stmt = stmt.where(SecurityFinding.status.notin_(sorted(ACTIONABLE)))
        if severity:
            stmt = stmt.where(SecurityFinding.severity == severity)
        return list((await self.session.exec(
            stmt.order_by(SecurityFinding.created_at.desc()).limit(limit))).all())

    async def cases(self, project_ids: Sequence[int], *, name_contains: Optional[str],
                    priority: Optional[str], limit: int) -> List[Any]:
        from app.models import TestCase
        stmt = select(TestCase).where(TestCase.project_id.in_(list(project_ids)))
        if name_contains:
            stmt = stmt.where(TestCase.name.ilike(f"%{name_contains}%"))
        if priority:
            stmt = stmt.where(TestCase.priority == priority)
        return list((await self.session.exec(stmt.order_by(TestCase.name).limit(limit))).all())

    async def project(self, project_id: int) -> Any:
        from app.models import Project
        return await self.session.get(Project, project_id)

    async def snapshot(self, project: Any, days: int) -> Dict[str, Any]:
        from app.services import reporting
        return await reporting.project_snapshot(self.session, project, days=days)

    async def readiness(self, project: Any) -> Optional[Dict[str, Any]]:
        from app.services import reporting
        return await reporting.readiness(self.session, project)


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------

Handler = Callable[[ToolContext, Dict[str, Any]], Awaitable[Dict[str, Any]]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    signature: str
    description: str
    handler: Handler


def _item(kind: str, record_id: int, project_id: Optional[int], label: str,
          fields: Dict[str, Any], **meta: Any) -> Dict[str, Any]:
    """One record for the model, plus `_meta` (kept server-side for citations)."""
    ref = make_ref(kind, record_id)
    return {"ref": ref, **fields,
            "_meta": {"ref": ref, "kind": kind, "id": int(record_id),
                      "project_id": project_id, "label": _clip(label, 120), **meta}}


def _pname(ctx: ToolContext, pid: Optional[int]) -> Optional[str]:
    return ctx.project_names.get(pid) if pid is not None else None


def _run_item(ctx: ToolContext, r: Any) -> Dict[str, Any]:
    label = f"Run #{r.id} · {r.suite_name or r.test_case_name or 'run'} · {_status(r.status)}"
    return _item("run", r.id, r.project_id, label, {
        "project": _pname(ctx, r.project_id), "suite": _clip(r.suite_name, 120),
        "case": _clip(r.test_case_name, 120), "status": _status(r.status),
        "created_at": _iso(r.created_at), "browser": r.browser,
        "passed": r.passed_tests, "failed": r.failed_tests, "total": r.total_tests,
        "git_branch": _clip(r.git_branch, 80), "git_commit": (r.git_commit or "")[:12] or None,
        "triggered_by": _status(r.triggered_by) if r.triggered_by is not None else None,
        "error": _clip(r.error_message),
    })


def _result_item(ctx: ToolContext, res: Any, run: Any) -> Dict[str, Any]:
    label = f"{res.test_name} · {_status(res.status)} (run #{run.id})"
    return _item("result", res.id, run.project_id, label, {
        "run_ref": make_ref("run", run.id), "project": _pname(ctx, run.project_id),
        "test": _clip(res.test_name, 160), "status": _status(res.status),
        "at": _iso(run.created_at), "duration_ms": res.duration_ms,
        "error": _clip(res.error_message), "retries": res.retry_count,
        "flaky": bool(res.is_flaky), "cluster_ref": make_ref("cluster", res.cluster_id)
        if res.cluster_id else None,
        "case_ref": make_ref("case", res.test_case_id) if res.test_case_id else None,
    }, run_id=run.id)


async def t_list_projects(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    needle = (_str_arg(args, "name_contains") or "").lower()
    items = [_item("project", pid, pid, name, {"name": name})
             for pid, name in sorted(ctx.project_names.items(), key=lambda kv: kv[1].lower())
             if pid in ctx.allowed_project_ids and needle in name.lower()]
    return {"items": items[:MAX_ITEMS]}


async def t_project_overview(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    pid = _scope(ctx, args, required=True)
    if len(pid) != 1:
        raise ToolError("project_id is required")
    days = _int_arg(args, "days", default=7, lo=1, hi=90)
    project = await ctx.data.project(pid[0])
    if project is None or project.id not in ctx.allowed_project_ids:
        raise ToolError(f"project {pid[0]} {NOT_ACCESSIBLE}")
    snap = await ctx.data.snapshot(project, days)
    items = [_item("project", project.id, project.id, project.name, {"name": project.name})]
    if snap.get("last_run"):
        lr = snap["last_run"]
        items.append(_item("run", lr["id"], project.id, f"Run #{lr['id']} · {lr['status']}",
                           {"last_run_status": lr["status"], "at": lr.get("created_at")}))
    for c in snap.get("clusters") or []:
        items.append(_item("cluster", c["id"], c.get("project_id"), c.get("title") or "cluster", {
            "title": _clip(c.get("title")), "category": c.get("category"),
            "status": c.get("status"), "occurrences": c.get("occurrences"),
            "last_seen_at": c.get("last_seen_at")}))
    rd = snap.get("readiness")
    return {
        "summary": {
            "window_days": snap.get("window_days"), "headline": _clip(snap.get("headline")),
            "current": snap.get("current"), "previous": snap.get("previous"),
            "top_failing_tests": snap.get("top_failing"), "open_clusters": snap.get("open_clusters"),
            "flaky_cases": snap.get("flaky_count"), "incidents": snap.get("incidents"),
            "release_verdict": rd.get("verdict") if rd else None,
        },
        "items": items,
    }


async def t_list_runs(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    pids = _scope(ctx, args)
    status = _choice_arg(args, "status", ("passed", "failed", "error", "cancelled", "timed_out", "running", "pending"))
    hours = _int_arg(args, "hours", default=168, lo=1, hi=24 * 90)
    limit = _int_arg(args, "limit", default=10, lo=1, hi=MAX_ITEMS)
    runs = await ctx.data.runs(pids, status=status, since=ctx.now - timedelta(hours=hours),
                               suite_contains=_str_arg(args, "suite_contains"),
                               branch=_str_arg(args, "git_branch"), limit=limit)
    return {"items": [_run_item(ctx, r) for r in runs]}


async def t_get_run(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    run_id = _int_arg(args, "run_id")
    if run_id is None:
        raise ToolError("run_id is required")
    run = await ctx.data.run(run_id)
    _check_record(ctx, run, "run", run_id)
    results = await ctx.data.run_results(run.id, failing_only=True, limit=15)
    items = [_run_item(ctx, run)] + [_result_item(ctx, res, run) for res in results]
    analysis = run.ai_analysis if isinstance(run.ai_analysis, dict) else {}
    reports = [
        {"result_ref": make_ref("result", rp["result_id"]) if rp.get("result_id") else None,
         "test": _clip(rp.get("test_name"), 160), "category": rp.get("root_cause_category"),
         "summary": _clip(rp.get("summary")), "suggested_fix": _clip(rp.get("suggested_fix")),
         "confidence": rp.get("confidence")}
        for rp in (analysis.get("reports") or [])[:8] if isinstance(rp, dict)]
    return {"summary": {"failure_analysis": _clip(analysis.get("summary")) if analysis else None,
                        "analysis_reports": reports},
            "items": items}


async def t_search_results(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    pids = _scope(ctx, args)
    status = _choice_arg(args, "status", ("failed", "passed", "any"), default="failed")
    hours = _int_arg(args, "hours", default=72, lo=1, hi=24 * 90)
    limit = _int_arg(args, "limit", default=15, lo=1, hi=MAX_ITEMS)
    rows = await ctx.data.results(pids, name_contains=_str_arg(args, "test_name_contains"),
                                  status=status, since=ctx.now - timedelta(hours=hours),
                                  limit=limit)
    return {"items": [_result_item(ctx, res, run) for res, run in rows]}


async def t_list_flaky_tests(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.flake_policy import quarantine_state
    pids = _scope(ctx, args)
    limit = _int_arg(args, "limit", default=20, lo=1, hi=MAX_ITEMS)
    items = []
    for rec, case in await ctx.data.flaky(pids, limit=limit):
        items.append(_item("case", case.id, case.project_id, f"{case.name} (flaky)", {
            "project": _pname(ctx, case.project_id), "test": _clip(case.name, 160),
            "flake_score": round(float(rec.flake_score or 0), 2), "samples": rec.sample_count,
            "quarantine": quarantine_state(rec),
            "quarantine_expires_at": _iso(rec.quarantine_expires_at),
            "last_observed_at": _iso(rec.last_observed_at),
            "last_failure": _clip(rec.last_failure_message)}, suite_id=case.test_suite_id))
    return {"items": items}


def _cluster_item(ctx: ToolContext, c: Any) -> Dict[str, Any]:
    return _item("cluster", c.id, c.project_id, c.title or f"Cluster {c.id}", {
        "project": _pname(ctx, c.project_id), "title": _clip(c.title), "category": c.category,
        "status": c.status, "occurrences": c.occurrence_count,
        "first_seen_at": _iso(c.first_seen_at), "last_seen_at": _iso(c.last_seen_at),
        "sample_error": _clip(c.sample_error)})


async def t_list_failure_clusters(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    pids = _scope(ctx, args)
    status = _choice_arg(args, "status", ("open", "resolved", "ignored", "any"), default="open")
    limit = _int_arg(args, "limit", default=10, lo=1, hi=MAX_ITEMS)
    return {"items": [_cluster_item(ctx, c)
                      for c in await ctx.data.clusters(pids, status=status, limit=limit)]}


async def t_get_failure_cluster(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    cid = _int_arg(args, "cluster_id")
    if cid is None:
        raise ToolError("cluster_id is required")
    c = await ctx.data.cluster(cid)
    _check_record(ctx, c, "cluster", cid)
    rows = await ctx.data.cluster_results(c.id, c.project_id, limit=10)
    return {"items": [_cluster_item(ctx, c)] + [_result_item(ctx, res, run) for res, run in rows]}


async def t_release_readiness(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    pid = _scope(ctx, args, required=True)
    if len(pid) != 1:
        raise ToolError("project_id is required")
    project = await ctx.data.project(pid[0])
    if project is None or project.id not in ctx.allowed_project_ids:
        raise ToolError(f"project {pid[0]} {NOT_ACCESSIBLE}")
    rd = await ctx.data.readiness(project)
    if rd is None:
        return {"summary": {"verdict": None, "note": "readiness could not be computed"}, "items": []}
    return {"items": [_item("readiness", project.id, project.id,
                            f"Release readiness · {project.name}", {
                                "project": project.name, "verdict": rd.get("verdict"),
                                "git_commit": (rd.get("git_commit") or "")[:12] or None,
                                "reasons": [_clip(r) for r in (rd.get("reasons") or [])[:10]],
                                "failing_checks": (rd.get("failing_checks") or [])[:10]})]}


async def t_list_security_findings(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    pids = _scope(ctx, args)
    severity = _choice_arg(args, "severity", ("critical", "high", "medium", "low", "info"))
    status = _choice_arg(args, "status", ("open", "closed", "any"), default="open")
    limit = _int_arg(args, "limit", default=15, lo=1, hi=MAX_ITEMS)
    items = [_item("finding", f.id, f.project_id, f.title, {
        "project": _pname(ctx, f.project_id), "title": _clip(f.title), "severity": f.severity,
        "category": f.category, "status": f.status, "created_at": _iso(f.created_at),
        "due_at": _iso(f.due_at), "run_ref": make_ref("run", f.run_id) if f.run_id else None})
        for f in await ctx.data.findings(pids, severity=severity, status=status, limit=limit)]
    return {"items": items}


async def t_find_cases(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    pids = _scope(ctx, args)
    priority = _choice_arg(args, "priority", ("critical", "high", "medium", "low"))
    limit = _int_arg(args, "limit", default=15, lo=1, hi=MAX_ITEMS)
    items = [_item("case", c.id, c.project_id, c.name, {
        "project": _pname(ctx, c.project_id), "name": _clip(c.name, 160),
        "priority": c.priority, "tags": [_clip(t, 40) for t in (c.tags or [])[:10]],
        "steps": len(c.steps or [])}, suite_id=c.test_suite_id)
        for c in await ctx.data.cases(pids, name_contains=_str_arg(args, "name_contains"),
                                      priority=priority, limit=limit)]
    return {"items": items}


TOOLS: Dict[str, ToolSpec] = {t.name: t for t in [
    ToolSpec("list_projects", "list_projects(name_contains?)",
             "projects you can query, with their ids", t_list_projects),
    ToolSpec("project_overview", "project_overview(project_id, days=7)",
             "pass rate and trend, top failing tests, open failure clusters, flaky count, "
             "last run and release verdict for one project", t_project_overview),
    ToolSpec("list_runs", "list_runs(project_id?, status?, hours=168, suite_contains?, "
             "git_branch?, limit=10)",
             "recent runs, newest first; status is passed|failed|error|cancelled|timed_out|running|pending",
             t_list_runs),
    ToolSpec("get_run", "get_run(run_id)",
             "one run with its failing results and any stored failure analysis", t_get_run),
    ToolSpec("search_results", "search_results(project_id?, test_name_contains?, "
             "status=failed|passed|any, hours=72, limit=15)",
             "individual test results across runs, newest first", t_search_results),
    ToolSpec("list_flaky_tests", "list_flaky_tests(project_id?, limit=20)",
             "flaky or quarantined test cases, worst first", t_list_flaky_tests),
    ToolSpec("list_failure_clusters", "list_failure_clusters(project_id?, "
             "status=open|resolved|ignored|any, limit=10)",
             "groups of failures sharing one signature", t_list_failure_clusters),
    ToolSpec("get_failure_cluster", "get_failure_cluster(cluster_id)",
             "one failure cluster with its recent results", t_get_failure_cluster),
    ToolSpec("release_readiness", "release_readiness(project_id)",
             "release verdict for the newest commit, with reasons and failing gate checks",
             t_release_readiness),
    ToolSpec("list_security_findings", "list_security_findings(project_id?, severity?, "
             "status=open|closed|any, limit=15)",
             "security findings (open = open or in progress)", t_list_security_findings),
    ToolSpec("find_cases", "find_cases(project_id?, name_contains?, priority?, limit=15)",
             "test cases by name or priority", t_find_cases),
]}


def tool_catalog() -> str:
    return "\n".join(f"- {t.signature}: {t.description}" for t in TOOLS.values())


# --------------------------------------------------------------------------
# Running a tool
# --------------------------------------------------------------------------


def _clean_args(args: Any) -> Dict[str, Any]:
    """Arguments as stored and echoed: scalars only, short strings, at most 8."""
    out: Dict[str, Any] = {}
    if not isinstance(args, dict):
        return out
    for k, v in list(args.items())[:8]:
        if isinstance(v, (int, float, bool)) or v is None:
            out[str(k)[:40]] = v
        elif isinstance(v, str):
            out[str(k)[:40]] = redact_for_llm(v[:100])
    return out


def _fit(payload: Dict[str, Any]) -> tuple:
    """Drop trailing items until the serialised payload fits MAX_RESULT_CHARS."""
    truncated = False
    while len(json.dumps(payload, default=str)) > MAX_RESULT_CHARS and payload.get("items"):
        payload["items"] = payload["items"][:-1]
        truncated = True
    if len(json.dumps(payload, default=str)) > MAX_RESULT_CHARS:
        payload.pop("summary", None)
        truncated = True
    if truncated:
        payload["truncated"] = True
    return payload, truncated


async def run_tool(ctx: ToolContext, name: str, args: Any) -> ToolResult:
    clean = _clean_args(args)
    result = ToolResult(tool=str(name)[:60], args=clean)
    spec = TOOLS.get(name) if isinstance(name, str) else None
    if spec is None:
        result.error = f"unknown tool; available: {', '.join(TOOLS)}"
        result.payload = {"error": result.error}
        return result
    try:
        raw = await spec.handler(ctx, clean)
    except ToolError as exc:
        result.error = str(exc)
        result.payload = {"error": result.error}
        return result
    except Exception as exc:  # noqa: BLE001 — one failing tool must not kill the answer
        print(f"[Assistant] tool {name} failed: {exc}")
        result.error = "the tool failed; try another approach"
        result.payload = {"error": result.error}
        return result

    items = []
    for it in (raw.get("items") or [])[:MAX_ITEMS]:
        meta = it.pop("_meta", None) or {}
        # Post-filter: nothing outside the caller's scope reaches the model,
        # whatever the handler or data layer returned.
        if meta.get("project_id") not in ctx.allowed_project_ids:
            continue
        items.append(it)
        result.refs[meta["ref"]] = meta
        if meta["project_id"] not in result.project_ids:
            result.project_ids.append(meta["project_id"])
    payload: Dict[str, Any] = {"items": items}
    if raw.get("summary") is not None:
        payload["summary"] = raw["summary"]
    payload, result.truncated = _fit(redact_tree(payload))
    # Refs whose items were cut for size were never seen: not citable.
    kept = {i.get("ref") for i in payload.get("items", [])}
    result.refs = {r: m for r, m in result.refs.items() if r in kept}
    for m in result.refs.values():
        m["label"] = redact_for_llm(m.get("label"))
    result.payload = payload
    return result
