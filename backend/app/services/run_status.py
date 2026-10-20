"""Which statuses mean what (WP15f).

Two questions used to share one answer, ``(PASSED, FAILED, ERROR)``:

* **Is the run over?** — ``TERMINAL``. Waits, purges, "already final" checks
  and finalize use this. CANCELLED and TIMED_OUT are over.
* **Did it produce a verdict?** — ``VERDICT``. Pass rates, gates, readiness,
  flake scores and trends use this. CANCELLED and TIMED_OUT did not: a run
  someone stopped, or one the platform gave up on, says nothing about the
  application, so it counts neither as a pass nor as a failure (it is missing
  data — the gate reads "insufficient data", never "pass").

``FAILING`` stays FAILED + ERROR: an ERROR is still a verdict that something
is wrong (setup failed, the case could not run as written).

Error kinds explain a non-passing RESULT. Workers from WP15f on send one; for
older workers (decision 6) the aggregator derives it from status + message.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

from app.models import TestStatus

LIVE = (TestStatus.PENDING, TestStatus.RUNNING)
VERDICT = (TestStatus.PASSED, TestStatus.FAILED, TestStatus.ERROR)
NO_VERDICT = (TestStatus.CANCELLED, TestStatus.TIMED_OUT)
TERMINAL = VERDICT + NO_VERDICT
FAILING = (TestStatus.FAILED, TestStatus.ERROR)

# Lowercase wire values, for code comparing API/JSON strings.
LIVE_VALUES = frozenset(s.value for s in LIVE)
VERDICT_VALUES = frozenset(s.value for s in VERDICT)
TERMINAL_VALUES = frozenset(s.value for s in TERMINAL)
FAILING_VALUES = frozenset(s.value for s in FAILING)

ERROR_KINDS = ("test", "setup", "infrastructure", "timeout", "cancelled", "not_executed")

# Worker wire status → TestStatus. Anything unknown is ERROR, as before.
WIRE_STATUS = {
    "passed": TestStatus.PASSED,
    "failed": TestStatus.FAILED,
    "error": TestStatus.ERROR,
    "cancelled": TestStatus.CANCELLED,
    "timed_out": TestStatus.TIMED_OUT,
}


def from_wire(value: Optional[str]) -> TestStatus:
    return WIRE_STATUS.get((value or "error").lower(), TestStatus.ERROR)


def is_terminal(status) -> bool:
    return _coerce(status) in TERMINAL


def has_verdict(status) -> bool:
    return _coerce(status) in VERDICT


def _coerce(status) -> Optional[TestStatus]:
    if isinstance(status, TestStatus):
        return status
    try:
        return TestStatus(str(getattr(status, "value", status)).lower())
    except ValueError:
        return None


# Messages old workers produce for failures that are not the test's fault.
# Kept narrow on purpose: an unrecognised message is a test failure, which is
# what every pre-WP15f result already was.
_INFRA = re.compile(
    r"browserType\.launch|Executable doesn't exist|Target page, context or browser has been closed"
    r"|browser has disconnected|ECONNREFUSED|ENOTFOUND|EAI_AGAIN|socket hang up"
    r"|Unknown step type|not supported by this worker|out of memory|ENOSPC",
    re.IGNORECASE)
_TIMEOUT = re.compile(r"exceeded MAX_JOB_DURATION_MS|job time budget is exhausted|Test execution timed out",
                      re.IGNORECASE)
_SETUP = re.compile(r"suite setup|setup step|setup hook", re.IGNORECASE)
_NOT_EXECUTED = re.compile(r"manual (test|case).*not executed|not executed", re.IGNORECASE)


def derive_error_kind(status, error_message: Optional[str] = None,
                      reported: Optional[str] = None) -> Optional[str]:
    """The error kind to store for a result. None for passes / live rows.

    A kind the worker reported wins when it is one we know; otherwise it is
    derived from the message.
    """
    st = _coerce(status)
    if st == TestStatus.CANCELLED:
        return "cancelled"
    if st == TestStatus.TIMED_OUT:
        return "timeout"
    if st not in FAILING:
        return None
    if reported in ERROR_KINDS:
        return reported
    msg = error_message or ""
    if _TIMEOUT.search(msg):
        return "timeout"
    if _NOT_EXECUTED.search(msg):
        return "not_executed"
    if _INFRA.search(msg):
        return "infrastructure"
    if st == TestStatus.ERROR and _SETUP.search(msg):
        return "setup"
    return "test"


def normalise_result(wire_status: Optional[str], error_message: Optional[str] = None,
                     reported_kind: Optional[str] = None) -> Tuple[TestStatus, Optional[str]]:
    """(status, error_kind) for one ingested result, always consistent.

    A failing result whose kind is ``timeout`` (the platform's limit — old
    workers report those as plain failures) becomes TIMED_OUT, and one whose
    kind is ``cancelled`` becomes CANCELLED, so status and kind never
    disagree and neither is counted as a verdict on the application.
    """
    status = from_wire(wire_status)
    kind = derive_error_kind(status, error_message, reported_kind)
    if status in FAILING and kind == "timeout":
        status = TestStatus.TIMED_OUT
    elif status in FAILING and kind == "cancelled":
        status = TestStatus.CANCELLED
    return status, kind


def run_outcome(result_statuses, failed_counter: int = 0) -> TestStatus:
    """Terminal status of a run whose jobs have all reported.

    FAILING beats TIMED_OUT beats CANCELLED beats PASSED: one real failure
    is the most useful thing to say about a run; a run where some case never
    got a verdict is not a clean pass. ``failed_counter`` is the Redis
    progress count (every non-pass), kept as a fallback.
    """
    seen = {_coerce(s) for s in result_statuses}
    if seen & set(FAILING):
        return TestStatus.FAILED
    # The counter said something failed and no no-verdict result explains
    # it (e.g. a job that errored before reporting its cases): FAILED, as
    # the counter alone decided before WP15f.
    if failed_counter > 0 and not seen & set(NO_VERDICT):
        return TestStatus.FAILED
    if TestStatus.TIMED_OUT in seen:
        return TestStatus.TIMED_OUT
    if TestStatus.CANCELLED in seen:
        return TestStatus.CANCELLED
    return TestStatus.PASSED
