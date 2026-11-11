"""Test cycles: the pure rules (API in app/api/cycles.py).

A cycle is a chosen set of cases for one release plus the runs made for it.
Its progress is never stored. For each case it is read from the results that
case has among the cycle's runs:

* rows without a verdict are ignored — a cancelled result, a manual case
  nobody has recorded yet, a result still running;
* the NEWEST row per browser/device counts (so a green re-run replaces a red
  one, and chromium cannot hide firefox) — the same rule release readiness
  uses (`release_readiness.classify_case`);
* the worst of those is the case's state: failed > blocked > skipped > passed.

"blocked" is what a tester records when a case could not be executed; an
automated ERROR or TIMED_OUT result means the same thing and maps to it.
"skipped" is a deliberate manual outcome. A case with no counted row is
`not_run`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Sequence

PASSED, FAILED, BLOCKED, SKIPPED, NOT_RUN = "passed", "failed", "blocked", "skipped", "not_run"
STATES = (PASSED, FAILED, BLOCKED, SKIPPED, NOT_RUN)
# Worst first.
_SEVERITY = {FAILED: 3, BLOCKED: 2, SKIPPED: 1, PASSED: 0}

OPEN, CLOSED = "open", "closed"

MAX_ITEMS = 2000
MAX_NAME = 120
MAX_DESCRIPTION = 4000
MAX_CYCLES_PER_PROJECT = 500


class CycleError(ValueError):
    pass


class Evidence(NamedTuple):
    result_id: int
    case_id: int
    status: str                 # TestStatus value, lower case
    run_id: int
    browser: Optional[str]
    device: Optional[str]
    at: Optional[datetime]
    manual: bool = False
    outcome: Optional[str] = None      # manual: passed|failed|blocked|skipped|None
    tester_id: Optional[int] = None


def evidence_state(e: Evidence) -> Optional[str]:
    """The state one result stands for, or None when it carries no verdict."""
    if e.manual:
        return e.outcome if e.outcome in _SEVERITY else None
    return {"passed": PASSED, "failed": FAILED, "error": BLOCKED,
            "timed_out": BLOCKED}.get((e.status or "").lower())


def _recency(e: Evidence) -> tuple:
    return (e.at or datetime.min, e.result_id)


def classify(rows: Iterable[Evidence]) -> Dict[str, Any]:
    """State of one case in a cycle from its results in the cycle's runs."""
    newest: Dict[tuple, tuple] = {}
    for e in rows:
        state = evidence_state(e)
        if state is None:
            continue
        key = (e.browser or "", e.device or "")
        if key not in newest or _recency(e) > _recency(newest[key][0]):
            newest[key] = (e, state)
    if not newest:
        return {"status": NOT_RUN, "run_id": None, "result_id": None, "at": None,
                "tester_id": None, "configurations": 0, "run_ids": []}
    # Worst state; among equals the newest row is the one to open.
    e, state = max(newest.values(), key=lambda p: (_SEVERITY[p[1]], _recency(p[0])))
    return {"status": state, "run_id": e.run_id, "result_id": e.result_id, "at": e.at,
            "tester_id": e.tester_id, "configurations": len(newest),
            # Every run that holds a counted (newest-per-configuration) result:
            # what a sign-off of the cycle rests on, not only the worst one.
            "run_ids": sorted({row.run_id for row, _ in newest.values()})}


def summarize(states: Sequence[str]) -> Dict[str, Any]:
    counts = {s: 0 for s in STATES}
    for s in states:
        counts[s if s in counts else NOT_RUN] += 1
    total = len(states)
    executed = total - counts[NOT_RUN]
    return {
        "total": total, "executed": executed, "counts": counts,
        # None, not 0, for an empty cycle: "0% done" would be a statement.
        "percent_executed": round(100 * executed / total) if total else None,
        "percent_passed": round(100 * counts[PASSED] / total) if total else None,
    }


def by_assignee(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Progress per assignee (None = unassigned), busiest first."""
    groups: Dict[Optional[int], List[str]] = {}
    for it in items:
        groups.setdefault(it.get("assignee_id"), []).append(it["status"])
    out = [{"assignee_id": uid, **summarize(states)} for uid, states in groups.items()]
    out.sort(key=lambda g: (g["assignee_id"] is None, -g["total"], g["assignee_id"] or 0))
    return out


def is_remaining(status: str) -> bool:
    """Still needs someone's attention in this cycle."""
    return status != PASSED


def clean_name(name: Optional[str]) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise CycleError("A cycle needs a name")
    if len(name) > MAX_NAME:
        raise CycleError(f"The name is longer than {MAX_NAME} characters")
    return name


def check_dates(start, due) -> None:
    if start and due and due < start:
        raise CycleError("The due date is before the start date")


def root_suites(parents: Dict[int, Optional[int]]) -> Dict[int, int]:
    """suite id -> its top-level ancestor. `parents` is {suite: parent or None}.
    A broken chain (missing parent, loop) ends where it breaks."""
    roots: Dict[int, int] = {}
    for sid in parents:
        seen, cur = set(), sid
        while cur in parents and parents[cur] is not None and parents[cur] in parents \
                and cur not in seen:
            seen.add(cur)
            cur = parents[cur]
        roots[sid] = cur
    return roots
