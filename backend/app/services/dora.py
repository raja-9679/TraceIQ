"""DORA metrics from explicit deployment events (WP12b, decision R3) — pure logic.

Inputs come from three places and every output says which were used:

* `Deployment` rows posted by CI — the only source of "a deployment happened".
* `Incident` rows (WP6) — change failures and time to restore.
* Failed `TestRun`s on the deployment's environment — a post-deploy run that
  fails within 24 h also marks the deployment as a change failure.

Attribution rules (kept deliberately simple so a reader can check them):

* An incident belongs to at most one deployment: the one whose commit/version
  the incident names, else the latest deployment that started within 24 h
  before it. Incidents carry no environment, so they are matched project-wide.
* A failed run belongs to the latest deployment on the same environment
  (NULL matches NULL) that preceded it by at most 24 h.
* A deployment whose own status is `failed` is a change failure.

Lead time = deployed_at − commit_at when CI sent the commit time, else
deployed_at − the first TestRun carrying that git_commit. Medians are used
for lead time and time to restore, as the DORA research does.

Bands follow the 2023 State of DevOps report's published thresholds; the
thresholds are returned with the result so the page can show them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Dict, Iterable, List, Optional

CHANGE_FAILURE_WINDOW = timedelta(hours=24)

# value thresholds per band, best first. Frequency is deployments/day
# (higher is better); the others are "at most" (lower is better).
THRESHOLDS: Dict[str, Any] = {
    "deployment_frequency": {
        "unit": "deployments per day",
        "elite": "on demand (at least once a day)",
        "high": "between once a day and once a week",
        "medium": "between once a week and once a month",
        "low": "less than once a month",
    },
    "lead_time": {
        "unit": "hours (median)",
        "elite": "less than one day", "high": "one day to one week",
        "medium": "one week to one month", "low": "more than one month",
    },
    "change_failure_rate": {
        "unit": "percent of deployments",
        "elite": "5% or less", "high": "10% or less", "medium": "15% or less",
        "low": "more than 15%",
    },
    "time_to_restore": {
        "unit": "hours (median)",
        "elite": "less than one hour", "high": "less than one day",
        "medium": "one day to one week", "low": "more than one week",
    },
}


def frequency_band(per_day: float) -> str:
    if per_day >= 1:
        return "elite"
    if per_day >= 1 / 7:
        return "high"
    if per_day >= 1 / 30:
        return "medium"
    return "low"


def lead_time_band(hours: float) -> str:
    if hours < 24:
        return "elite"
    if hours <= 24 * 7:
        return "high"
    if hours <= 24 * 30:
        return "medium"
    return "low"


def change_failure_band(pct: float) -> str:
    if pct <= 5:
        return "elite"
    if pct <= 10:
        return "high"
    if pct <= 15:
        return "medium"
    return "low"


def restore_band(hours: float) -> str:
    if hours < 1:
        return "elite"
    if hours < 24:
        return "high"
    if hours <= 24 * 7:
        return "medium"
    return "low"


@dataclass
class Dep:
    id: int
    deployed_at: datetime
    environment_id: Optional[int] = None
    git_commit: Optional[str] = None
    version: Optional[str] = None
    commit_at: Optional[datetime] = None
    status: str = "success"
    # filled by compute()
    failure_causes: List[str] = field(default_factory=list)
    lead_time_hours: Optional[float] = None
    lead_time_source: Optional[str] = None


@dataclass
class Inc:
    id: int
    started_at: datetime
    resolved_at: Optional[datetime] = None
    git_commit: Optional[str] = None
    release: Optional[str] = None


@dataclass
class FailedRun:
    id: int
    created_at: datetime
    environment_id: Optional[int] = None


def _hours(td: timedelta) -> float:
    return td.total_seconds() / 3600.0


def _same_commit(a: Optional[str], b: Optional[str]) -> bool:
    """Exact, or one is an abbreviation (>= 7 chars) of the other."""
    if not a or not b:
        return False
    a, b = a.strip().lower(), b.strip().lower()
    if a == b:
        return True
    short, full = (a, b) if len(a) < len(b) else (b, a)
    return len(short) >= 7 and full.startswith(short)


def _incident_owner(inc: Inc, deps: List[Dep]) -> Optional[Dep]:
    candidates = [d for d in deps
                  if d.deployed_at <= inc.started_at < d.deployed_at + CHANGE_FAILURE_WINDOW]
    if not candidates:
        return None
    named = [d for d in candidates
             if _same_commit(d.git_commit, inc.git_commit)
             or (inc.release and d.version and inc.release.strip() == d.version.strip())]
    pool = named or candidates
    return max(pool, key=lambda d: d.deployed_at)


def _run_owner(run: FailedRun, deps: List[Dep]) -> Optional[Dep]:
    candidates = [d for d in deps
                  if d.environment_id == run.environment_id
                  and d.deployed_at <= run.created_at < d.deployed_at + CHANGE_FAILURE_WINDOW]
    return max(candidates, key=lambda d: d.deployed_at) if candidates else None


def compute(*, deployments: Iterable[Dep], incidents: Iterable[Inc],
            failed_runs: Iterable[FailedRun], first_run_at: Dict[str, datetime],
            window_start: datetime, window_end: datetime) -> Dict[str, Any]:
    """All four metrics for deployments in [window_start, window_end).

    `incidents` and `failed_runs` may extend 24 h past the window end so a
    deployment late in the window can still be marked failed; time to restore
    only uses incidents that started inside the window."""
    deps = sorted((d for d in deployments if window_start <= d.deployed_at < window_end),
                  key=lambda d: d.deployed_at)
    incidents = list(incidents)
    days = max((window_end - window_start).total_seconds() / 86400.0, 1e-9)

    # --- change failures --------------------------------------------------
    for d in deps:
        if (d.status or "").lower() == "failed":
            d.failure_causes.append("deployment_failed")
    incident_links: Dict[int, int] = {}
    for inc in incidents:
        owner = _incident_owner(inc, deps)
        if owner:
            incident_links[inc.id] = owner.id
            if "incident" not in owner.failure_causes:
                owner.failure_causes.append("incident")
    run_links = 0
    for run in failed_runs:
        owner = _run_owner(run, deps)
        if owner:
            run_links += 1
            if "failed_run" not in owner.failure_causes:
                owner.failure_causes.append("failed_run")

    # --- lead time --------------------------------------------------------
    sources = {"commit_at": 0, "first_run": 0}
    for d in deps:
        start, source = None, None
        if d.commit_at:
            start, source = d.commit_at, "commit_at"
        elif d.git_commit and d.git_commit in first_run_at:
            start, source = first_run_at[d.git_commit], "first_run"
        if start is not None and start <= d.deployed_at:
            d.lead_time_hours = round(_hours(d.deployed_at - start), 2)
            d.lead_time_source = source
            sources[source] += 1
    leads = [d.lead_time_hours for d in deps if d.lead_time_hours is not None]

    # --- time to restore --------------------------------------------------
    in_window = [i for i in incidents if window_start <= i.started_at < window_end]
    restores = [_hours(i.resolved_at - i.started_at) for i in in_window
                if i.resolved_at and i.resolved_at >= i.started_at]
    unresolved = sum(1 for i in in_window if not i.resolved_at)

    no_deploys = "No deployments recorded in this window. Send them from CI with POST /api/projects/{id}/deployments."
    n = len(deps)
    per_day = n / days
    failed = [d for d in deps if d.failure_causes]

    frequency = {
        "deployments": n,
        "per_day": round(per_day, 3),
        "per_week": round(per_day * 7, 2),
        "band": frequency_band(per_day) if n else None,
        "missing": None if n else no_deploys,
    }
    lead = {
        "median_hours": round(median(leads), 2) if leads else None,
        "samples": len(leads),
        "sources": sources,
        "band": lead_time_band(median(leads)) if leads else None,
        "missing": None if leads else (
            no_deploys if not n else
            "No deployment in this window has a start time: send commit_at, or run "
            "tests with the same git_commit before deploying."),
    }
    cfr_pct = round(100.0 * len(failed) / n, 1) if n else None
    cfr = {
        "percent": cfr_pct,
        "failed_deployments": len(failed),
        "deployments": n,
        "causes": {
            "deployment_failed": sum(1 for d in failed if "deployment_failed" in d.failure_causes),
            "incident": sum(1 for d in failed if "incident" in d.failure_causes),
            "failed_run": sum(1 for d in failed if "failed_run" in d.failure_causes),
        },
        "linked_incidents": len(incident_links),
        "linked_failed_runs": run_links,
        "band": change_failure_band(cfr_pct) if cfr_pct is not None else None,
        "missing": None if n else no_deploys,
    }
    restore = {
        "median_hours": round(median(restores), 2) if restores else None,
        "resolved_incidents": len(restores),
        "unresolved_incidents": unresolved,
        "band": restore_band(median(restores)) if restores else None,
        "missing": None if restores else (
            "No incidents started in this window." if not in_window else
            "No incident that started in this window has been resolved yet."),
    }
    return {
        "deployment_frequency": frequency,
        "lead_time": lead,
        "change_failure_rate": cfr,
        "time_to_restore": restore,
        "deployments": [{
            "id": d.id, "deployed_at": d.deployed_at, "environment_id": d.environment_id,
            "git_commit": d.git_commit, "version": d.version, "status": d.status,
            "failure_causes": d.failure_causes, "lead_time_hours": d.lead_time_hours,
            "lead_time_source": d.lead_time_source,
        } for d in reversed(deps)],
        "thresholds": THRESHOLDS,
    }
