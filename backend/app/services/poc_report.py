"""Proof-of-concept report (WP13b, decision C4).

One page for a trial: what was set up, what ran, what it found, and a
LABELLED estimate of the manual effort the automated executions replaced:

    hours saved = automated case executions x minutes per manual execution / 60
    money       = hours saved x hourly rate          (only if a rate is given)

Both assumptions are the user's, editable, and printed next to the number;
nothing here assumes a currency (the user types a code). Manual-executor runs
are not "automated executions" and are left out of the estimate.

The caller decides which projects are covered (only ones the viewer can
see); nothing here checks access. The public (share-link) payload is built by
whitelisting counts and names: no error text, URLs, emails or cluster/finding
titles.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

DEFAULT_MINUTES = 5.0
MAX_MINUTES = 480.0
MAX_RATE = 100000.0
MAX_RANGE_DAYS = 366
SEVERITIES = ("critical", "high", "medium", "low", "info")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def parse_range(start: Optional[str], end: Optional[str],
                today: Optional[date] = None) -> tuple:
    """(start_date, end_date) inclusive; defaults to the last 30 days."""
    today = today or datetime.utcnow().date()
    try:
        e = date.fromisoformat(end) if end else today
        s = date.fromisoformat(start) if start else e - timedelta(days=29)
    except (TypeError, ValueError):
        raise ValueError("start and end must be dates (YYYY-MM-DD)")
    if s > e:
        raise ValueError("start must be on or before end")
    if (e - s).days + 1 > MAX_RANGE_DAYS:
        raise ValueError(f"the range can be at most {MAX_RANGE_DAYS} days")
    return s, e


def parse_assumptions(minutes: Any = None, hourly_rate: Any = None,
                      currency: Any = None) -> Dict[str, Any]:
    try:
        m = DEFAULT_MINUTES if minutes in (None, "") else float(minutes)
    except (TypeError, ValueError):
        raise ValueError("minutes_per_execution must be a number")
    if not 0 < m <= MAX_MINUTES:
        raise ValueError(f"minutes_per_execution must be above 0 and at most {int(MAX_MINUTES)}")
    rate = None
    if hourly_rate not in (None, ""):
        try:
            rate = float(hourly_rate)
        except (TypeError, ValueError):
            raise ValueError("hourly_rate must be a number")
        if not 0 <= rate <= MAX_RATE:
            raise ValueError(f"hourly_rate must be between 0 and {int(MAX_RATE)}")
    cur = None
    if currency not in (None, ""):
        cur = str(currency).strip().upper()
        if not _CURRENCY.match(cur):
            raise ValueError("currency must be a three-letter code, for example EUR")
    if rate is not None and cur is None:
        raise ValueError("give a currency code with the hourly rate")
    return {"minutes_per_execution": m, "hourly_rate": rate, "currency": cur}


def _fmt_num(v: float) -> str:
    return f"{v:,.0f}" if v == int(v) else f"{v:,.1f}"


def estimate(executions: int, assumptions: Dict[str, Any]) -> Dict[str, Any]:
    """The time-saved estimate with its label. Always labelled an estimate:
    it is arithmetic on the user's assumptions, not a measurement."""
    m = float(assumptions.get("minutes_per_execution") or DEFAULT_MINUTES)
    n = max(0, int(executions or 0))
    hours = round(n * m / 60.0, 1)
    label = (f"Estimated {_fmt_num(hours)} hours of manual testing replaced "
             f"({n:,} automated case executions x {_fmt_num(m)} min each)")
    out: Dict[str, Any] = {
        "automated_executions": n, "minutes_per_execution": m, "hours_saved": hours,
        "label": label, "money": None,
        "basis": "Estimate from your assumptions, not a measurement.",
    }
    rate, cur = assumptions.get("hourly_rate"), assumptions.get("currency")
    if rate is not None and cur:
        amount = round(hours * float(rate), 2)
        out["money"] = {
            "amount": amount, "currency": cur, "hourly_rate": float(rate),
            "label": f"Estimated {cur} {amount:,.2f} at {cur} {float(rate):,.2f} per hour",
        }
    return out


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def _key(v: Any) -> str:
    return str(v.value if hasattr(v, "value") else v)


async def build(session, workspace: Any, projects: Sequence[Any], start: date, end: date,
                assumptions: Dict[str, Any]) -> Dict[str, Any]:
    from sqlalchemy import func, or_, String
    from sqlalchemy import cast as sa_cast
    from sqlmodel import select

    from app.models import (
        ApiKey, ExecutorType, FailureCluster, Incident, IncidentWebhook, IssueTrackerConfig,
        PagingChannel, ProjectEnvironment, SecurityFinding, StatusPage, TestCase, TestCaseResult,
        TestRun, TestSchedule, TestStatus, TestSuite, WorkspaceWebhook,
    )
    from app.services.release_readiness import flaky_case_names
    from app.services.run_scope import counted

    ids = [p.id for p in projects]
    since = datetime.combine(start, datetime.min.time())
    until = datetime.combine(end + timedelta(days=1), datetime.min.time())
    finished = (TestStatus.PASSED, TestStatus.FAILED, TestStatus.ERROR)
    failing = (TestStatus.FAILED, TestStatus.ERROR)

    async def scalar(q) -> int:
        return int((await session.exec(q)).one() or 0) if ids else 0

    # --- setup inventory: names and counts only ---
    by_executor: Dict[str, int] = {}
    if ids:
        for ex, n in (await session.exec(
                select(TestCase.executor, func.count(TestCase.id))
                .where(TestCase.project_id.in_(ids)).group_by(TestCase.executor))).all():
            by_executor[_key(ex)] = int(n)
    envs = (await session.exec(select(ProjectEnvironment.name).where(
        ProjectEnvironment.project_id.in_(ids)).order_by(ProjectEnvironment.name))).all() if ids else []
    monitors = (await session.exec(select(TestSchedule.name).where(
        TestSchedule.project_id.in_(ids), TestSchedule.is_monitor == True,  # noqa: E712
        TestSchedule.is_active == True).order_by(TestSchedule.name))).all() if ids else []  # noqa: E712
    trackers = (await session.exec(select(IssueTrackerConfig.provider).where(
        IssueTrackerConfig.workspace_id == workspace.id))).all()
    integrations = {
        "issue_trackers": sorted({str(p) for p in trackers}),
        "outbound_webhooks": int((await session.exec(select(func.count()).select_from(WorkspaceWebhook).where(
            WorkspaceWebhook.workspace_id == workspace.id,
            or_(WorkspaceWebhook.project_id.is_(None), WorkspaceWebhook.project_id.in_(ids or [-1]))))).one() or 0),
        "incident_webhooks": await scalar(select(func.count()).select_from(IncidentWebhook).where(
            IncidentWebhook.project_id.in_(ids))),
        "paging_channels": await scalar(select(func.count()).select_from(PagingChannel).where(
            PagingChannel.project_id.in_(ids))),
        "status_pages": await scalar(select(func.count()).select_from(StatusPage).where(
            StatusPage.project_id.in_(ids))),
        "api_keys": int((await session.exec(select(func.count()).select_from(ApiKey).where(
            ApiKey.workspace_id == workspace.id, ApiKey.revoked_at.is_(None),
            or_(ApiKey.project_id.is_(None), ApiKey.project_id.in_(ids or [-1]))))).one() or 0),
    }
    inventory = {
        "projects": [p.name for p in projects],
        "suites": await scalar(select(func.count()).select_from(TestSuite).where(TestSuite.project_id.in_(ids))),
        "cases": sum(by_executor.values()),
        "cases_by_executor": dict(sorted(by_executor.items())),
        "environments": list(envs),
        "monitors": list(monitors),
        "integrations": integrations,
    }

    # --- activity ---
    # counted(): validation runs (WP16c) are not activity a PoC can claim.
    in_range = (TestRun.project_id.in_(ids), TestRun.created_at >= since, TestRun.created_at < until,
                counted())
    runs_total = passed_runs = 0
    if ids:
        for st, n in (await session.exec(select(TestRun.status, func.count(TestRun.id)).where(
                *in_range, TestRun.status.in_(finished)).group_by(TestRun.status))).all():
            runs_total += int(n)
            if _key(st) == "passed":
                passed_runs += int(n)
    automated = TestRun.executor != ExecutorType.MANUAL
    executions = await scalar(select(func.count(TestCaseResult.id))
                              .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
                              .where(*in_range, automated, TestCaseResult.status.in_(finished)))
    manual_executions = await scalar(select(func.count(TestCaseResult.id))
                                     .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
                                     .where(*in_range, TestRun.executor == ExecutorType.MANUAL,
                                            TestCaseResult.status.in_(finished)))
    browsers = sorted({b for b in (await session.exec(
        select(TestRun.browser).distinct().where(*in_range))).all() if b}) if ids else []
    devices = sorted({d for d in (await session.exec(
        select(TestRun.device).distinct().where(*in_range))).all() if d}) if ids else []
    activity = {
        "runs": runs_total, "passed_runs": passed_runs,
        "pass_rate": round(100.0 * passed_runs / runs_total, 1) if runs_total else None,
        "case_executions": executions, "manual_case_executions": manual_executions,
        "browsers": browsers, "devices": devices,
    }

    # --- value found ---
    failures = await scalar(select(func.count(TestCaseResult.id))
                            .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
                            .where(*in_range, TestCaseResult.status.in_(failing)))
    clusters = await scalar(select(func.count()).select_from(FailureCluster).where(
        FailureCluster.project_id.in_(ids), FailureCluster.last_seen_at >= since,
        FailureCluster.first_seen_at < until))
    findings = {s: 0 for s in SEVERITIES}
    if ids:
        # One logical finding = one fingerprint, however many scans saw it.
        logical = func.coalesce(SecurityFinding.fingerprint, sa_cast(SecurityFinding.id, String))
        for sev, n in (await session.exec(
                select(SecurityFinding.severity, func.count(func.distinct(logical))).where(
                    SecurityFinding.project_id.in_(ids), SecurityFinding.created_at >= since,
                    SecurityFinding.created_at < until).group_by(SecurityFinding.severity))).all():
            k = str(sev or "info").lower()
            findings[k if k in findings else "info"] += int(n)
    flaky = 0
    for pid in ids:
        flaky += len(await flaky_case_names(session, pid, until))
    inc_where = (Incident.project_id.in_(ids), Incident.started_at >= since, Incident.started_at < until)
    incidents_total = await scalar(select(func.count()).select_from(Incident).where(*inc_where))
    incidents_linked = await scalar(select(func.count()).select_from(Incident).where(
        *inc_where, or_(Incident.matched_case_id.is_not(None), Incident.matched_cluster_id.is_not(None),
                        Incident.regression_case_id.is_not(None), Incident.escaped_case_id.is_not(None))))
    value = {
        "failures_caught": failures, "failure_clusters": clusters,
        "security_findings": findings, "security_findings_total": sum(findings.values()),
        "flaky_tests": flaky, "incidents": incidents_total, "incidents_linked": incidents_linked,
    }

    return {
        "workspace_id": workspace.id, "workspace_name": workspace.name,
        "start": start.isoformat(), "end": end.isoformat(),
        "days": (end - start).days + 1,
        "generated_at": datetime.utcnow().isoformat(),
        "project_ids": ids,
        "inventory": inventory, "activity": activity, "value": value,
        "assumptions": assumptions,
        "estimate": estimate(executions, assumptions),
    }


# ---------------------------------------------------------------------------
# Public payload (share link kind poc_report): whitelist
# ---------------------------------------------------------------------------


def _n(v: Any) -> Any:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _names(v: Any) -> List[str]:
    return [str(x) for x in (v or []) if isinstance(x, str)]


def public_payload(r: Dict[str, Any]) -> Dict[str, Any]:
    inv, act, val, est = r.get("inventory") or {}, r.get("activity") or {}, r.get("value") or {}, r.get("estimate") or {}
    integ = inv.get("integrations") or {}
    money = est.get("money")
    return {
        "kind": "poc_report",
        "workspace_name": r.get("workspace_name"),
        "start": r.get("start"), "end": r.get("end"), "days": _n(r.get("days")),
        "generated_at": r.get("generated_at"),
        "inventory": {
            "projects": _names(inv.get("projects")),
            "suites": _n(inv.get("suites")), "cases": _n(inv.get("cases")),
            "cases_by_executor": {str(k): _n(v) for k, v in (inv.get("cases_by_executor") or {}).items()},
            "environments": _names(inv.get("environments")),
            "monitors": _names(inv.get("monitors")),
            "integrations": {
                "issue_trackers": _names(integ.get("issue_trackers")),
                **{k: _n(integ.get(k)) for k in ("outbound_webhooks", "incident_webhooks",
                                                  "paging_channels", "status_pages", "api_keys")},
            },
        },
        "activity": {
            **{k: _n(act.get(k)) for k in ("runs", "passed_runs", "pass_rate", "case_executions",
                                           "manual_case_executions")},
            "browsers": _names(act.get("browsers")), "devices": _names(act.get("devices")),
        },
        "value": {
            **{k: _n(val.get(k)) for k in ("failures_caught", "failure_clusters", "security_findings_total",
                                           "flaky_tests", "incidents", "incidents_linked")},
            "security_findings": {s: _n((val.get("security_findings") or {}).get(s)) for s in SEVERITIES},
        },
        "estimate": {
            **{k: _n(est.get(k)) for k in ("automated_executions", "minutes_per_execution", "hours_saved")},
            "label": est.get("label"), "basis": est.get("basis"),
            "money": ({"amount": _n(money.get("amount")), "currency": money.get("currency"),
                       "hourly_rate": _n(money.get("hourly_rate")), "label": money.get("label")}
                      if money else None),
        },
    }
