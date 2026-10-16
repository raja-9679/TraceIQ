"""Flake trend + pattern helpers (WP12b, roadmap A3 rest) — pure logic.

Patterns are computed from TestCaseResult history over a bounded window. A
result is *flaky* when the aggregator flagged it (`is_flaky`: it failed and
then passed on retry, or its case's flake score says so); *failed* when it
ended failed/error. Rates are per finished result in the bucket, so a browser
that runs ten times as often doesn't look ten times as flaky.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

# A case counts as flaky in the daily snapshot at this score — the same line
# the Flaky tests page draws between "mostly stable" and "sometimes fails".
FLAKY_SCORE = 0.33
SNAPSHOT_RETENTION_DAYS = 180
UNKNOWN = "unknown"


def _pct(part: int, whole: int) -> Optional[float]:
    return round(100.0 * part / whole, 1) if whole else None


def rate_buckets(rows: Iterable[Tuple[Any, int, int, int]],
                 label: Optional[Callable[[Any], str]] = None) -> List[Dict[str, Any]]:
    """(key, total, flaky, failed) rows → buckets sorted by flaky rate, then
    volume. `None` keys become "unknown"."""
    merged: Dict[Any, List[int]] = {}
    for key, total, flaky, failed in rows:
        k = UNKNOWN if key is None or key == "" else key
        m = merged.setdefault(k, [0, 0, 0])
        m[0] += int(total or 0)
        m[1] += int(flaky or 0)
        m[2] += int(failed or 0)
    out = [{
        "key": k,
        "label": label(k) if label and k != UNKNOWN else str(k),
        "total": t, "flaky": f, "failed": fl,
        "flaky_pct": _pct(f, t), "failed_pct": _pct(fl, t),
    } for k, (t, f, fl) in merged.items()]
    out.sort(key=lambda b: (b["flaky_pct"] or 0, b["total"]), reverse=True)
    return out


def hour_buckets(rows: Iterable[Tuple[Any, int, int, int]]) -> List[Dict[str, Any]]:
    """Zero-filled 0..23 UTC hours, in hour order."""
    by_hour = {b["key"]: b for b in rate_buckets(
        (int(h) if h is not None else None, t, f, fl) for h, t, f, fl in rows)}
    out = []
    for h in range(24):
        b = by_hour.get(h) or {"key": h, "total": 0, "flaky": 0, "failed": 0,
                               "flaky_pct": None, "failed_pct": None}
        b["label"] = f"{h:02d}:00 UTC"
        out.append(b)
    return out


def step_type_buckets(rows: Iterable[Tuple[bool, Optional[str], int]]) -> List[Dict[str, Any]]:
    """(is_flaky, failing step type, count) → per step type counts of flaky
    and failed results. Results without per-step evidence land in "unknown"."""
    merged: Dict[str, List[int]] = {}
    for is_flaky, step_type, count in rows:
        k = (step_type or UNKNOWN).strip() or UNKNOWN
        m = merged.setdefault(k, [0, 0])
        m[0 if is_flaky else 1] += int(count or 0)
    out = [{"step_type": k, "flaky": f, "failed": fl, "total": f + fl}
           for k, (f, fl) in merged.items()]
    out.sort(key=lambda b: (b["step_type"] == UNKNOWN, -b["total"], b["step_type"]))
    return out


def case_snapshot_rows(records: Iterable[Any]) -> Dict[int, Dict[str, Any]]:
    """FlakeRecords → one entry per case: max score, quarantine OR-ed, the
    case-level row's sample count (falls back to the largest)."""
    out: Dict[int, Dict[str, Any]] = {}
    for r in records:
        e = out.setdefault(r.test_case_id, {"flake_score": 0.0, "is_quarantined": False,
                                            "sample_count": 0})
        e["flake_score"] = max(e["flake_score"], float(r.flake_score or 0.0))
        e["is_quarantined"] = e["is_quarantined"] or bool(r.is_quarantined)
        e["sample_count"] = max(e["sample_count"], int(r.sample_count or 0))
    return out


def project_snapshot(case_rows: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    scores = [e["flake_score"] for e in case_rows.values()]
    return {
        "flaky_cases": sum(1 for s in scores if s >= FLAKY_SCORE),
        "quarantined_cases": sum(1 for e in case_rows.values() if e["is_quarantined"]),
        "avg_flake_score": round(sum(scores) / len(scores), 4) if scores else None,
    }
