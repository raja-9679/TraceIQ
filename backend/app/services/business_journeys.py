"""Business journey board (WP12c): health of a named group of cases.

A journey's health comes from each case's results in the window (newest
first), not from a stored rollup:

* a case is **failing** when its latest result failed/errored;
* **flaky** when it is quarantined, its latest result was marked flaky, or
  its recent results mix passes and failures while the latest passed;
* **passing** when the latest passed and nothing above applies;
* **not_run** when it has no result in the window.

The journey is failing if any case is failing, else flaky if any case is
flaky, else passing if any case ran, else not_run. Cases that never ran in an
otherwise passing journey are counted, not hidden.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence

CRITICALITIES = ("low", "medium", "high", "critical")
MAX_JOURNEY_CASES = 200
MAX_REQUIREMENT_REFS = 50
FLAKY_LOOKBACK = 10
TREND_DAYS = 14


def _st(v: Any) -> str:
    return str(getattr(v, "value", v) or "")


def case_state(history: Sequence[Dict[str, Any]], quarantined: bool) -> Dict[str, Any]:
    """`history`: this case's results newest first [{status, created_at,
    is_flaky, run_id}]."""
    if not history:
        return {"state": "not_run", "last_run_at": None, "last_run_id": None, "last_status": None}
    latest = history[0]
    last = _st(latest.get("status"))
    recent = {_st(h.get("status")) for h in history[:FLAKY_LOOKBACK]}
    if last in ("failed", "error"):
        state = "failing"
    elif quarantined or latest.get("is_flaky") or ("passed" in recent and recent & {"failed", "error"}):
        state = "flaky"
    elif last == "passed":
        state = "passing"
    else:  # pending / running only
        state = "not_run"
    at = latest.get("created_at")
    return {"state": state, "last_status": last, "last_run_id": latest.get("run_id"),
            "last_run_at": at.isoformat() if isinstance(at, datetime) else at}


def rollup(states: Iterable[str]) -> str:
    s = list(states)
    if "failing" in s:
        return "failing"
    if "flaky" in s:
        return "flaky"
    if "passing" in s:
        return "passing"
    return "not_run"


def trend(history: Iterable[Dict[str, Any]], now: datetime, days: int = TREND_DAYS
          ) -> List[Dict[str, Any]]:
    """Daily pass rate of all the journey's results, oldest day first."""
    start = (now - timedelta(days=days - 1)).date()
    buckets = {start + timedelta(days=i): [0, 0] for i in range(days)}
    for h in history:
        at = h.get("created_at")
        if not isinstance(at, datetime):
            continue
        b = buckets.get(at.date())
        if b is None:
            continue
        st = _st(h.get("status"))
        if st in ("passed", "failed", "error"):
            b[1] += 1
            b[0] += st == "passed"
    return [{"date": d.isoformat(), "passed": p, "total": t,
             "pass_rate": round(100.0 * p / t, 1) if t else None}
            for d, (p, t) in sorted(buckets.items())]


def journey_health(case_ids: Sequence[int], names: Dict[int, str],
                   history_by_case: Dict[int, List[Dict[str, Any]]],
                   quarantined: Iterable[int], now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.utcnow()
    q = set(quarantined)
    cases = []
    for cid in case_ids:
        st = case_state(history_by_case.get(cid, []), cid in q)
        cases.append({"id": cid, "name": names.get(cid), "quarantined": cid in q, **st})
    states = [c["state"] for c in cases]
    last_at = max((c["last_run_at"] for c in cases if c["last_run_at"]), default=None)
    all_hist = [h for cid in case_ids for h in history_by_case.get(cid, [])]
    return {
        "health": rollup(states),
        "cases": cases,
        "counts": {k: states.count(k) for k in ("passing", "failing", "flaky", "not_run")},
        "last_run_at": last_at,
        "trend": trend(all_hist, now),
    }


def clean_refs(refs: Any) -> List[str]:
    if refs is None:
        return []
    if not isinstance(refs, list):
        raise ValueError("requirement_refs must be a list")
    out: List[str] = []
    for r in refs:
        s = str(r or "").strip()[:200]
        if s and s not in out:
            out.append(s)
    if len(out) > MAX_REQUIREMENT_REFS:
        raise ValueError(f"at most {MAX_REQUIREMENT_REFS} requirement refs")
    return out
