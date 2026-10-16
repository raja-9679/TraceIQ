"""Monitor SLO maths (WP12b, decision R7) — pure logic.

A monitor check is the SLI event: good when the run passed. Over a rolling
window (7/30/90 days, default 30):

* compliance        = good / total checks, as a percent
* error budget      = the share of checks allowed to fail: (1 - target) × total
* budget remaining  = 1 − bad / budget, as a percent. Negative = overspent.
* burn rate         = observed failure rate / allowed failure rate. 1.0 spends
                      the budget exactly over the window; 2.0 twice as fast.
                      Reported for the whole window and for the last 24 h, the
                      fast-burn signal.

Latency comes from the check's run duration (MonitorCheck.run_id →
TestRun.duration_ms); checks without one are left out of the percentile and the
report says how many had data. p95 is nearest-rank.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

DEFAULT_TARGET = 99.9
WINDOWS = (7, 30, 90)
DEFAULT_WINDOW = 30


@dataclass
class Check:
    checked_at: datetime
    is_up: bool
    region: Optional[str] = None
    duration_ms: Optional[float] = None


def percentile(values: List[float], q: float) -> Optional[float]:
    """Nearest-rank percentile (q in 0..100); None for no values."""
    if not values:
        return None
    vals = sorted(values)
    rank = max(1, math.ceil(q / 100.0 * len(vals)))
    return float(vals[rank - 1])


def budget(total: int, bad: int, target: float) -> Dict[str, Any]:
    allowed_rate = max(0.0, 1.0 - target / 100.0)
    allowed = allowed_rate * total
    if total == 0:
        return {"allowed_failures": 0.0, "remaining_pct": None, "burn_rate": None}
    observed_rate = bad / total
    if allowed_rate == 0:
        # A 100 % target leaves no budget: any failure is a breach.
        return {"allowed_failures": 0.0,
                "remaining_pct": 100.0 if bad == 0 else None,
                "burn_rate": 0.0 if bad == 0 else None}
    return {
        "allowed_failures": round(allowed, 2),
        "remaining_pct": round(100.0 * (1.0 - bad / allowed), 1),
        "burn_rate": round(observed_rate / allowed_rate, 2),
    }


def _summary(checks: List[Check], target: float, latency_target: Optional[int],
             now: datetime) -> Dict[str, Any]:
    total = len(checks)
    good = sum(1 for c in checks if c.is_up)
    bad = total - good
    recent = [c for c in checks if c.checked_at >= now - timedelta(hours=24)]
    recent_bad = sum(1 for c in recent if not c.is_up)
    b = budget(total, bad, target)
    durations = [c.duration_ms for c in checks if c.duration_ms is not None]
    p95 = percentile(durations, 95)
    compliance = round(100.0 * good / total, 3) if total else None
    burn_24h = budget(len(recent), recent_bad, target)["burn_rate"]

    if compliance is None:
        status = "no_data"
    elif compliance < target:
        status = "breached"
    elif burn_24h is not None and burn_24h > 1:
        status = "at_risk"
    else:
        status = "meeting"
    latency: Dict[str, Any] = {
        "p95_ms": round(p95, 1) if p95 is not None else None,
        "target_ms": latency_target,
        "checks_with_latency": len(durations),
        "met": (p95 <= latency_target) if (p95 is not None and latency_target) else None,
        "missing": None if durations else "No latency data: these checks have no run duration.",
    }
    return {
        "checks": total, "good": good, "bad": bad,
        "compliance_pct": compliance,
        "error_budget": {**b, "spent_failures": bad},
        "burn_rate_24h": burn_24h,
        "status": status,
        "latency": latency,
    }


def report(checks: Iterable[Check], *, target: Optional[float], latency_target: Optional[int],
           window_days: int, now: datetime) -> Dict[str, Any]:
    start = now - timedelta(days=window_days)
    rows = [c for c in checks if start <= c.checked_at <= now]
    eff_target = float(target) if target is not None else DEFAULT_TARGET
    out = _summary(rows, eff_target, latency_target, now)
    out.update({
        "window_days": window_days,
        "window_start": start,
        "target_pct": eff_target,
        "target_is_default": target is None,
        "missing": None if rows else f"No monitor checks in the last {window_days} days.",
    })
    regions = sorted({c.region for c in rows if c.region})
    out["regions"] = [
        {"region": r, **_summary([c for c in rows if c.region == r], eff_target, latency_target, now)}
        for r in regions
    ] if len(regions) > 1 else []
    by_day: Dict[str, List[int]] = {}
    for c in rows:
        d = by_day.setdefault(c.checked_at.date().isoformat(), [0, 0])
        d[0] += 1
        d[1] += 1 if c.is_up else 0
    out["daily"] = [
        {"date": (start + timedelta(days=i + 1)).date().isoformat(),
         "checks": by_day.get((start + timedelta(days=i + 1)).date().isoformat(), [0, 0])[0],
         "good": by_day.get((start + timedelta(days=i + 1)).date().isoformat(), [0, 0])[1]}
        for i in range(window_days)
    ]
    return out
