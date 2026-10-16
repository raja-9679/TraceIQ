"""Manual test cases, manual runs and release sign-off (WP12d, decision R5).

A manual case is an ordinary TestCase with executor="manual" whose steps are
`manual-step` entries — {"type": "manual-step", "value": <instruction>,
"params": {"expected": <expected result>}} — so cases, suites, tags,
priorities, requirements links and revisions all work unchanged.

A manual run is an ordinary TestRun with executor="manual" and one
TestCaseResult per case (result_kind="manual"). Reusing the run tables is the
point: run history, analytics, the quality gate and release readiness read
TestRun / TestCaseResult, so a tester's verdicts show up there next to the
automated results without a second reporting path. What a person records that
has no column — the four-way outcome, notes, per-step outcomes, tester,
attachments — lives in result_payload.

Two mappings follow from TestStatus being a native Postgres enum with five
values (adding one is an ALTER TYPE every status consumer would then have to
learn):

* outcome -> status: passed -> PASSED, failed -> FAILED, blocked -> ERROR
  (the case could not be executed — the same meaning ERROR has for a worker),
  skipped -> PENDING (no verdict; counted as neither passed nor failed).
* a manual run in progress stays PENDING, never RUNNING: the stale-run
  sweeper, the workspace concurrency cap and the ops dashboard all treat
  RUNNING as "a worker holds this", and would time out or count a run a
  person is working through over two days.

Workers are never handed manual cases (`exclude_manual` at dispatch); the
engine additionally refuses one cleanly if it ever gets one.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

MANUAL_STEP = "manual-step"
RESULT_KIND = "manual"

OUTCOMES = ("passed", "failed", "blocked", "skipped")
STEP_OUTCOMES = ("passed", "failed", "blocked", "skipped")
VERDICTS = ("approve", "reject")

MAX_STEPS = 200
MAX_INSTRUCTION = 2000
MAX_EXPECTED = 2000
MAX_NOTES = 5000
MAX_STEP_NOTE = 1000
MAX_RELEASE_LABEL = 120
MAX_SIGNOFF_RUNS = 50
MAX_CASE_DURATION_MS = 24 * 3600 * 1000

# Attachments: screenshots only. Checked by magic bytes, not by the client's
# Content-Type, because the file is later served back through a presigned URL
# and a "screenshot" that is really HTML would render in the viewer's browser.
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
MAX_ATTACHMENTS_PER_RESULT = 5
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"GIF87a", "image/gif", "gif"),
    (b"GIF89a", "image/gif", "gif"),
)


class ManualTestingError(ValueError):
    pass


def _executor_value(case: Any) -> str:
    ex = getattr(case, "executor", None)
    return getattr(ex, "value", ex) or "ui_playwright"


def is_manual_case(case: Any) -> bool:
    return _executor_value(case) == "manual"


def exclude_manual(cases: Sequence[Any]) -> List[Any]:
    """Cases a worker may execute. A manual case reaching a web worker would
    fail on its unknown step type and show a red result nobody caused."""
    return [c for c in cases if not is_manual_case(c)]


def _step_type(step: Any) -> Optional[str]:
    return step.get("type") if isinstance(step, dict) else getattr(step, "type", None)


def has_manual_steps(steps: Iterable[Any]) -> bool:
    return any(_step_type(s) == MANUAL_STEP for s in (steps or []))


def validate_manual_steps(steps: Sequence[Any]) -> None:
    """A manual case is manual steps only: one executor per case, and an
    automated step in it would never run (nothing dispatches the case)."""
    steps = list(steps or [])
    if len(steps) > MAX_STEPS:
        raise ManualTestingError(f"a manual case has at most {MAX_STEPS} steps")
    for i, s in enumerate(steps, 1):
        if _step_type(s) != MANUAL_STEP:
            raise ManualTestingError(
                f"step {i}: a manual case contains only '{MANUAL_STEP}' steps "
                f"(found '{_step_type(s)}') — put automated steps in their own case")
        value = s.get("value") if isinstance(s, dict) else getattr(s, "value", None)
        params = (s.get("params") if isinstance(s, dict) else getattr(s, "params", None)) or {}
        if not isinstance(value, str) or not value.strip():
            raise ManualTestingError(f"step {i}: the instruction (value) is required")
        if len(value) > MAX_INSTRUCTION:
            raise ManualTestingError(f"step {i}: instruction is longer than {MAX_INSTRUCTION} characters")
        expected = params.get("expected") if isinstance(params, dict) else None
        if expected is not None and (not isinstance(expected, str) or len(expected) > MAX_EXPECTED):
            raise ManualTestingError(f"step {i}: expected result must be text of at most {MAX_EXPECTED} characters")


def outcome_status(outcome: Optional[str]) -> str:
    """TestStatus value for a recorded outcome (see module docstring)."""
    return {"passed": "passed", "failed": "failed", "blocked": "error"}.get(outcome or "", "pending")


def summarize(outcomes: Sequence[Optional[str]]) -> Dict[str, Any]:
    """Run totals from the per-case outcomes. Failed and blocked both fail the
    run — a release whose cases could not be executed is not a pass. A run of
    only skipped cases has no evidence either way and is not a pass."""
    counts = {k: 0 for k in OUTCOMES}
    unrecorded = 0
    for o in outcomes:
        if o in counts:
            counts[o] += 1
        else:
            unrecorded += 1
    failed = counts["failed"] + counts["blocked"]
    status = "failed" if failed or not counts["passed"] else "passed"
    return {"counts": counts, "unrecorded": unrecorded, "passed": counts["passed"],
            "failed": failed, "status": status}


def validate_record(outcome: str, notes: Optional[str], step_results: Optional[list],
                    step_count: int, duration_ms: Optional[int]) -> List[Dict[str, Any]]:
    if outcome not in OUTCOMES:
        raise ManualTestingError(f"outcome must be one of {', '.join(OUTCOMES)}")
    if notes is not None and len(notes) > MAX_NOTES:
        raise ManualTestingError(f"notes are longer than {MAX_NOTES} characters")
    if duration_ms is not None and not (0 <= duration_ms <= MAX_CASE_DURATION_MS):
        raise ManualTestingError("duration_ms must be between 0 and 24 hours")
    out: List[Dict[str, Any]] = []
    for i, sr in enumerate(step_results or []):
        if i >= step_count:
            raise ManualTestingError("more step results than the case has steps")
        so = (sr or {}).get("outcome")
        note = (sr or {}).get("note")
        if so is not None and so not in STEP_OUTCOMES:
            raise ManualTestingError(f"step {i + 1}: outcome must be one of {', '.join(STEP_OUTCOMES)}")
        if note is not None and (not isinstance(note, str) or len(note) > MAX_STEP_NOTE):
            raise ManualTestingError(f"step {i + 1}: note must be text of at most {MAX_STEP_NOTE} characters")
        out.append({"index": i, "outcome": so, "note": (note or None)})
    return out


def sniff_image(head: bytes) -> Optional[Tuple[str, str]]:
    """(content_type, extension) for an accepted screenshot format, else None."""
    for magic, ctype, ext in _MAGIC:
        if head.startswith(magic):
            return ctype, ext
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


def executor_ids(runs: Iterable[Any], results: Iterable[Any],
                 case_ids: Optional[Set[int]] = None) -> Set[int]:
    """Everyone who executed the covered manual work: whoever started each
    manual run, and every tester who recorded a result in scope. Automated
    runs contribute nobody — a scheduler executing a suite is not a person
    whose work needs a second pair of eyes."""
    manual_runs = {r.id for r in runs if _executor_value(r) == "manual"}
    ids: Set[int] = {r.user_id for r in runs if r.id in manual_runs and r.user_id}
    for res in results:
        if res.test_run_id not in manual_runs:
            continue
        if case_ids is not None and res.test_case_id not in case_ids:
            continue
        tester = (res.result_payload or {}).get("tester_id")
        if isinstance(tester, int):
            ids.add(tester)
    return ids


def signoff_conflict(signer_id: int, executors: Set[int], required: bool) -> bool:
    """True when this person may not sign: separation of duties is on and
    they executed (part of) what they are signing for."""
    return bool(required) and signer_id in executors
