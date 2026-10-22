"""Quality reporting: the numbers behind the weekly digest, the executive
summary and the portfolio dashboard (WP12a).

One builder, three consumers, so a digest, a summary and a portfolio row for
the same project and window can never disagree:

* `project_snapshot` - one project over a window, with the previous window of
  the same length for the trend (pass rate, runs, failures), top failing
  tests, open failure clusters, flaky cases, the release-readiness verdict for
  the newest commit and incident counts.
* `workspace_snapshot` - the same over a set of projects. The CALLER decides
  the set (only projects the viewer can access); nothing here checks access.
* `portfolio_rows` - one compact row per project (7d / 30d pass rate, last
  run, verdict, flaky count, open security findings).

Pass rate is finished runs that passed / finished runs, the same definition
the old scheduled report and the analytics page use. "Failures" are failing
test results (a run with three failing tests is three failures), which is what
moves when a team fixes things.

The pure helpers (trend, headline, rendering) are unit-tested; the queries are
covered by tests/integration/test_reporting_db.py.
"""
from __future__ import annotations

import html
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import (
    FailureCluster, Incident, Project, SecurityFinding, TestCaseResult, TestRun, TestStatus,
)
from app.services.run_scope import counted

FINISHED = (TestStatus.PASSED, TestStatus.FAILED, TestStatus.ERROR)
FAILING = (TestStatus.FAILED, TestStatus.ERROR)
TOP_N = 5
MAX_WINDOW_DAYS = 90

VERDICT_TEXT = {
    "ready": "ready to release",
    "not_ready": "not ready to release",
    "insufficient_data": "not enough evidence to judge release readiness",
}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def pass_rate(passed: int, total: int) -> Optional[float]:
    """Percent, one decimal; None when nothing finished (0% would read as
    'everything failed')."""
    return round(100.0 * passed / total, 1) if total else None


def empty_stats() -> Dict[str, Any]:
    return {"runs": 0, "passed": 0, "failed": 0, "pass_rate": None, "failures": 0}


def combine(stats: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    out = empty_stats()
    for s in stats:
        for k in ("runs", "passed", "failed", "failures"):
            out[k] += int(s.get(k) or 0)
    out["pass_rate"] = pass_rate(out["passed"], out["runs"])
    return out


def trend(cur: Dict[str, Any], prev: Dict[str, Any]) -> Dict[str, Any]:
    """Change from the previous window. The pass-rate delta is None when
    either window had no finished runs: a delta from nothing is noise."""
    pr = None
    if cur.get("pass_rate") is not None and prev.get("pass_rate") is not None:
        pr = round(cur["pass_rate"] - prev["pass_rate"], 1)
    return {
        "pass_rate": pr,
        "runs": int(cur.get("runs") or 0) - int(prev.get("runs") or 0),
        "failures": int(cur.get("failures") or 0) - int(prev.get("failures") or 0),
    }


def fill_days(rows: Dict[date, Dict[str, int]], start: date, days: int) -> List[Dict[str, Any]]:
    """A point for every day in the window, including days without runs, so
    a chart's x-axis is time and not 'days something happened'."""
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        r = rows.get(d, {})
        runs, passed = int(r.get("runs", 0)), int(r.get("passed", 0))
        out.append({"date": d.isoformat(), "runs": runs, "passed": passed,
                    "pass_rate": pass_rate(passed, runs)})
    return out


def _signed(v: float, unit: str = "") -> str:
    return f"+{v:g}{unit}" if v > 0 else f"{v:g}{unit}"


def headline(s: Dict[str, Any]) -> str:
    """One or two plain-language sentences for a non-specialist reader."""
    cur, tr, days = s["current"], s["trend"], s["window_days"]
    period = "this week" if days == 7 else f"in the last {days} days"
    if not cur["runs"]:
        lead = f"No test runs finished {period}."
    else:
        lead = f"{cur['pass_rate']:g}% of {cur['runs']} runs passed {period}"
        if tr["pass_rate"] is None:
            lead += "."
        elif abs(tr["pass_rate"]) < 0.5:
            lead += ", about the same as the period before."
        else:
            word = "up" if tr["pass_rate"] > 0 else "down"
            lead += f", {word} {abs(tr['pass_rate']):g} points on the period before."
    parts = [lead]
    verdict = (s.get("readiness") or {}).get("verdict")
    if verdict in VERDICT_TEXT:
        parts.append(f"The latest build is {VERDICT_TEXT[verdict]}."
                     if verdict != "insufficient_data"
                     else f"There is {VERDICT_TEXT[verdict]}.")
    worries = []
    if s.get("open_clusters"):
        worries.append(f"{s['open_clusters']} open failure group"
                       + ("s" if s["open_clusters"] != 1 else ""))
    if s.get("flaky_count"):
        worries.append(f"{s['flaky_count']} flaky test" + ("s" if s["flaky_count"] != 1 else ""))
    esc = (s.get("incidents") or {}).get("escaped") or 0
    if esc:
        worries.append(f"{esc} escaped defect" + ("s" if esc != 1 else ""))
    if worries:
        parts.append("Watch: " + ", ".join(worries) + ".")
    return " ".join(parts)


def _delta_text(v: Optional[float], unit: str = "") -> str:
    if v is None:
        return "n/a"
    if v == 0:
        return "no change"
    return _signed(v, unit)


def render_digest_text(s: Dict[str, Any], link: Optional[str] = None) -> str:
    cur, tr = s["current"], s["trend"]
    pr = f"{cur['pass_rate']:g}%" if cur["pass_rate"] is not None else "n/a"
    lines = [
        f"TraceIQ quality digest - {s['project_name']} (last {s['window_days']} days)",
        "",
        s["headline"],
        "",
        f"Pass rate: {pr} ({_delta_text(tr['pass_rate'], ' pts')})",
        f"Runs: {cur['runs']} ({_delta_text(tr['runs'])})",
        f"Failures: {cur['failures']} ({_delta_text(tr['failures'])})",
        f"Flaky tests: {s['flaky_count']}",
        f"Open failure clusters: {s['open_clusters']}",
    ]
    verdict = (s.get("readiness") or {}).get("verdict")
    if verdict:
        lines.append(f"Release readiness: {verdict.replace('_', ' ')}")
    inc = s.get("incidents") or {}
    if inc.get("escaped"):
        lines.append(f"Escaped defects in the window: {inc['escaped']}")
    lines.append("")
    if s["top_failing"]:
        lines.append("Top failing tests:")
        lines += [f"  - {t['test_name']}: {t['failures']} failures" for t in s["top_failing"]]
    else:
        lines.append("No failing tests in the window.")
    if s.get("clusters"):
        lines.append("")
        lines.append("Open failure clusters:")
        lines += [f"  - {c['title']} ({c['category']}, {c['occurrences']}x)" for c in s["clusters"]]
    if link:
        lines += ["", f"Open in TraceIQ: {link}"]
    return "\n".join(lines)


def render_digest_html(s: Dict[str, Any], link: Optional[str] = None) -> str:
    e = html.escape
    cur, tr = s["current"], s["trend"]
    pr = f"{cur['pass_rate']:g}%" if cur["pass_rate"] is not None else "n/a"

    def kpi(label: str, value: Any, delta: str) -> str:
        return (f'<td style="padding:8px 16px 8px 0;vertical-align:top">'
                f'<div style="font-size:12px;color:#666">{e(label)}</div>'
                f'<div style="font-size:20px;font-weight:600">{e(str(value))}</div>'
                f'<div style="font-size:12px;color:#666">{e(delta)}</div></td>')

    verdict = (s.get("readiness") or {}).get("verdict")
    rows = [
        kpi("Pass rate", pr, _delta_text(tr["pass_rate"], " pts")),
        kpi("Runs", cur["runs"], _delta_text(tr["runs"])),
        kpi("Failures", cur["failures"], _delta_text(tr["failures"])),
        kpi("Flaky tests", s["flaky_count"], ""),
        kpi("Open clusters", s["open_clusters"], ""),
    ]
    parts = [
        '<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#111;max-width:640px">',
        f'<h2 style="margin:0 0 4px">Quality digest: {e(s["project_name"])}</h2>',
        f'<div style="color:#666;font-size:13px">Last {s["window_days"]} days</div>',
        f'<p style="font-size:15px">{e(s["headline"])}</p>',
        f'<table style="border-collapse:collapse"><tr>{"".join(rows)}</tr></table>',
    ]
    if verdict:
        parts.append(f'<p><strong>Release readiness:</strong> {e(verdict.replace("_", " "))}</p>')
    inc = s.get("incidents") or {}
    if inc.get("escaped"):
        parts.append(f'<p><strong>Escaped defects:</strong> {inc["escaped"]}</p>')
    if s["top_failing"]:
        items = "".join(f"<li>{e(t['test_name'])}: {t['failures']} failures</li>"
                        for t in s["top_failing"])
        parts.append(f"<h3 style=\"font-size:15px\">Top failing tests</h3><ul>{items}</ul>")
    else:
        parts.append("<p>No failing tests in the window.</p>")
    if s.get("clusters"):
        items = "".join(f"<li>{e(c['title'])} ({e(c['category'])}, {c['occurrences']}x)</li>"
                        for c in s["clusters"])
        parts.append(f"<h3 style=\"font-size:15px\">Open failure clusters</h3><ul>{items}</ul>")
    if link:
        parts.append(f'<p><a href="{e(link, quote=True)}">Open in TraceIQ</a></p>')
    parts.append("</div>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def _status_key(s: Any) -> str:
    return str(s.value if hasattr(s, "value") else s)


async def stats_by_project(session: AsyncSession, project_ids: Sequence[int],
                           since: datetime, until: datetime) -> Dict[int, Dict[str, Any]]:
    """Finished-run counts and failing-result counts per project, [since, until)."""
    out = {pid: empty_stats() for pid in project_ids}
    if not project_ids:
        return out
    rows = (await session.exec(
        select(TestRun.project_id, TestRun.status, func.count(TestRun.id))
        .where(TestRun.project_id.in_(list(project_ids)), counted(), TestRun.created_at >= since,
               TestRun.created_at < until, TestRun.status.in_(FINISHED))
        .group_by(TestRun.project_id, TestRun.status))).all()
    for pid, status, n in rows:
        st = out[pid]
        st["runs"] += int(n)
        if _status_key(status) == "passed":
            st["passed"] += int(n)
        else:
            st["failed"] += int(n)
    fails = (await session.exec(
        select(TestRun.project_id, func.count(TestCaseResult.id))
        .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
        .where(TestRun.project_id.in_(list(project_ids)), counted(), TestRun.created_at >= since,
               TestRun.created_at < until, TestCaseResult.status.in_(FAILING))
        .group_by(TestRun.project_id))).all()
    for pid, n in fails:
        out[pid]["failures"] = int(n)
    for st in out.values():
        st["pass_rate"] = pass_rate(st["passed"], st["runs"])
    return out


async def daily_series(session: AsyncSession, project_ids: Sequence[int],
                       since: datetime, days: int) -> List[Dict[str, Any]]:
    rows: Dict[date, Dict[str, int]] = {}
    if project_ids:
        day = func.date(TestRun.created_at)
        for d, status, n in (await session.exec(
                select(day, TestRun.status, func.count(TestRun.id))
                .where(TestRun.project_id.in_(list(project_ids)), counted(), TestRun.created_at >= since,
                       TestRun.status.in_(FINISHED))
                .group_by(day, TestRun.status))).all():
            if isinstance(d, str):
                d = date.fromisoformat(d)
            r = rows.setdefault(d, {"runs": 0, "passed": 0})
            r["runs"] += int(n)
            if _status_key(status) == "passed":
                r["passed"] += int(n)
    # From the window's first (partial) day THROUGH today: `days` points from
    # since.date() stopped at yesterday, so today's runs counted in the totals
    # but never appeared on the chart.
    start = since.date()
    return fill_days(rows, start, (datetime.utcnow().date() - start).days + 1)


async def top_failing(session: AsyncSession, project_ids: Sequence[int],
                      since: datetime, limit: int = TOP_N) -> List[Dict[str, Any]]:
    if not project_ids:
        return []
    rows = (await session.exec(
        select(TestCaseResult.test_name, func.count(TestCaseResult.id))
        .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
        .where(TestRun.project_id.in_(list(project_ids)), counted(), TestRun.created_at >= since,
               TestCaseResult.status.in_(FAILING))
        .group_by(TestCaseResult.test_name)
        .order_by(func.count(TestCaseResult.id).desc(), TestCaseResult.test_name)
        .limit(limit))).all()
    return [{"test_name": n, "failures": int(c)} for n, c in rows]


async def open_clusters(session: AsyncSession, project_ids: Sequence[int],
                        limit: int = TOP_N) -> tuple:
    """(count, the most frequent few). Titles come from failure signatures, so
    they may quote error text: internal views only, never a public payload."""
    if not project_ids:
        return 0, []
    where = (FailureCluster.project_id.in_(list(project_ids)),
             FailureCluster.status.in_(("open", "investigating")))
    count = (await session.exec(select(func.count()).select_from(FailureCluster).where(*where))).one()
    top = (await session.exec(
        select(FailureCluster).where(*where)
        .order_by(FailureCluster.occurrence_count.desc(), FailureCluster.id)
        .limit(limit))).all()
    return int(count or 0), [
        {"id": c.id, "project_id": c.project_id, "title": c.title, "category": c.category,
         "status": c.status, "occurrences": c.occurrence_count,
         "last_seen_at": c.last_seen_at.isoformat() if c.last_seen_at else None}
        for c in top]


async def flaky_count(session: AsyncSession, project_id: int, now: datetime) -> int:
    from app.services.release_readiness import flaky_case_names
    return len(await flaky_case_names(session, project_id, now))


async def readiness(session: AsyncSession, project: Project) -> Optional[Dict[str, Any]]:
    """Verdict for the newest commit, as the readiness page shows it. None if
    it cannot be computed (a report should not fail because one part did)."""
    from app.api.endpoints.quality import evaluate_gate_for_runs
    from app.services import release_readiness as rr
    try:
        _scope, commit, runs = await rr.resolve_runs(session, project.id, None, None)
        gate = await evaluate_gate_for_runs(session, project, runs, commit, None)
        verdict, reasons = rr.verdict_for(gate, bool(runs))
        return {"verdict": verdict, "reasons": reasons, "git_commit": commit,
                "failing_checks": [c.name for c in gate.checks if not c.passed]}
    except Exception:  # noqa: BLE001
        return None


async def incident_counts(session: AsyncSession, project_ids: Sequence[int],
                          since: datetime) -> Dict[str, int]:
    if not project_ids:
        return {"escaped": 0, "open": 0, "open_critical": 0}
    pids = list(project_ids)
    marked = func.coalesce(Incident.escaped_marked_at, Incident.created_at)
    escaped = (await session.exec(select(func.count()).select_from(Incident).where(
        Incident.project_id.in_(pids), Incident.escaped_defect == True,  # noqa: E712
        marked >= since))).one()
    open_ = (await session.exec(select(Incident.severity, func.count()).where(
        Incident.project_id.in_(pids), Incident.status != "resolved")
        .group_by(Incident.severity))).all()
    return {"escaped": int(escaped or 0), "open": sum(int(n) for _s, n in open_),
            "open_critical": sum(int(n) for s, n in open_ if s == "critical")}


async def last_runs(session: AsyncSession, project_ids: Sequence[int]) -> Dict[int, Dict[str, Any]]:
    if not project_ids:
        return {}
    newest = (select(func.max(TestRun.id)).where(TestRun.project_id.in_(list(project_ids)), counted(),
                                                 TestRun.status.in_(FINISHED))
              .group_by(TestRun.project_id))
    rows = (await session.exec(select(TestRun).where(TestRun.id.in_(newest)))).all()
    return {r.project_id: {"id": r.id, "status": _status_key(r.status),
                           "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows}


async def open_findings(session: AsyncSession, project_ids: Sequence[int]) -> Dict[int, int]:
    if not project_ids:
        return {}
    rows = (await session.exec(
        select(SecurityFinding.project_id, func.count(SecurityFinding.id))
        .where(SecurityFinding.project_id.in_(list(project_ids)),
               SecurityFinding.status == "open",
               # One finding per fingerprint, like the Security register: an
               # older occurrence keeps its "open" status once superseded.
               SecurityFinding.superseded_by_id == None)  # noqa: E711
        .group_by(SecurityFinding.project_id))).all()
    return {pid: int(n) for pid, n in rows}


def clamp_days(days: int) -> int:
    return max(1, min(int(days or 7), MAX_WINDOW_DAYS))


async def project_snapshot(session: AsyncSession, project: Project, days: int = 7,
                           now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.utcnow()
    days = clamp_days(days)
    since, prev_since = now - timedelta(days=days), now - timedelta(days=2 * days)
    pid = [project.id]
    cur = (await stats_by_project(session, pid, since, now))[project.id]
    prev = (await stats_by_project(session, pid, prev_since, since))[project.id]
    n_clusters, clusters = await open_clusters(session, pid)
    snap = {
        "scope": "project",
        "project_id": project.id,
        "project_name": project.name,
        "workspace_id": project.workspace_id,
        "window_days": days,
        "generated_at": now.isoformat(),
        "since": since.isoformat(),
        "current": cur,
        "previous": prev,
        "trend": trend(cur, prev),
        "daily": await daily_series(session, pid, since, days),
        "top_failing": await top_failing(session, pid, since),
        "open_clusters": n_clusters,
        "clusters": clusters,
        "flaky_count": await flaky_count(session, project.id, now),
        "readiness": await readiness(session, project),
        "incidents": await incident_counts(session, pid, since),
        "last_run": (await last_runs(session, pid)).get(project.id),
    }
    snap["headline"] = headline(snap)
    return snap


async def portfolio_rows(session: AsyncSession, projects: Sequence[Project],
                         now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    now = now or datetime.utcnow()
    ids = [p.id for p in projects]
    s7 = await stats_by_project(session, ids, now - timedelta(days=7), now)
    s30 = await stats_by_project(session, ids, now - timedelta(days=30), now)
    last = await last_runs(session, ids)
    findings = await open_findings(session, ids)
    rows = []
    for p in projects:
        rd = await readiness(session, p)
        rows.append({
            "project_id": p.id, "project_name": p.name,
            "pass_rate_7d": s7[p.id]["pass_rate"], "runs_7d": s7[p.id]["runs"],
            "pass_rate_30d": s30[p.id]["pass_rate"], "runs_30d": s30[p.id]["runs"],
            "failures_7d": s7[p.id]["failures"],
            "last_run": last.get(p.id),
            "verdict": rd["verdict"] if rd else None,
            "flaky_count": await flaky_count(session, p.id, now),
            "open_findings": findings.get(p.id, 0),
        })
    return rows


async def workspace_snapshot(session: AsyncSession, workspace: Any, projects: Sequence[Project],
                             days: int = 30, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Roll-up over `projects` - the caller passes only projects the viewer
    may see."""
    now = now or datetime.utcnow()
    days = clamp_days(days)
    since, prev_since = now - timedelta(days=days), now - timedelta(days=2 * days)
    ids = [p.id for p in projects]
    cur_by = await stats_by_project(session, ids, since, now)
    prev_by = await stats_by_project(session, ids, prev_since, since)
    cur, prev = combine(cur_by.values()), combine(prev_by.values())
    n_clusters, clusters = await open_clusters(session, ids)
    per_project, verdicts, flaky_total = [], {"ready": 0, "not_ready": 0, "insufficient_data": 0}, 0
    for p in projects:
        rd = await readiness(session, p)
        fc = await flaky_count(session, p.id, now)
        flaky_total += fc
        if rd and rd["verdict"] in verdicts:
            verdicts[rd["verdict"]] += 1
        per_project.append({
            "project_id": p.id, "project_name": p.name,
            "pass_rate": cur_by[p.id]["pass_rate"], "runs": cur_by[p.id]["runs"],
            "failures": cur_by[p.id]["failures"],
            "trend_pass_rate": trend(cur_by[p.id], prev_by[p.id])["pass_rate"],
            "verdict": rd["verdict"] if rd else None, "flaky_count": fc,
        })
    snap = {
        "scope": "workspace",
        "workspace_id": workspace.id,
        "workspace_name": workspace.name,
        "window_days": days,
        "generated_at": now.isoformat(),
        "since": since.isoformat(),
        "current": cur,
        "previous": prev,
        "trend": trend(cur, prev),
        "daily": await daily_series(session, ids, since, days),
        "top_failing": await top_failing(session, ids, since),
        "open_clusters": n_clusters,
        "clusters": clusters,
        "flaky_count": flaky_total,
        "verdicts": verdicts,
        "readiness": None,
        "incidents": await incident_counts(session, ids, since),
        "projects": per_project,
    }
    snap["headline"] = headline(snap)
    if projects:
        if verdicts["not_ready"]:
            snap["headline"] += (f" {verdicts['not_ready']} of {len(projects)} projects "
                                 "are not ready to release.")
        elif verdicts["ready"] == len(projects):
            snap["headline"] += " Every project is ready to release."
    return snap
