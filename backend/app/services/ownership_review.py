"""Test ownership and periodic review (WP16b, decision V5).

A case has an owner, a backup owner and a `last_reviewed_at`. How often it
must be reviewed comes from the suite chain's `review_interval_days` setting
(an inherited execution flag: the nearest suite that sets it wins; a suite
with `inherit_settings=false` cuts the chain), else DEFAULT_REVIEW_INTERVAL_DAYS.
`0` means "this subtree needs no periodic review".

Status of a case:
    not_required  interval 0
    never         never reviewed (last_reviewed_at NULL)
    overdue       last review older than the interval
    ok            reviewed within the interval
"needs review" = never | overdue.

An owner who lost access to the project (or was deactivated / erased) is
NOT cleared: the case shows `owner_state = "no_access"` so somebody notices
and reassigns it. Clearing it silently would make the case look unowned on
purpose.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional

DEFAULT_REVIEW_INTERVAL_DAYS = 90
MAX_REVIEW_INTERVAL_DAYS = 3650
NEEDS_REVIEW = ("never", "overdue")


class OwnershipError(ValueError):
    pass


def normalize_interval(value: Any) -> Optional[int]:
    """Validate a suite's `review_interval_days`. None / '' = unset (inherit)."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise OwnershipError("review_interval_days must be a whole number of days")
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise OwnershipError("review_interval_days must be a whole number of days")
    if n != value and not (isinstance(value, str) and value.strip().isdigit()):
        raise OwnershipError("review_interval_days must be a whole number of days")
    if n < 0 or n > MAX_REVIEW_INTERVAL_DAYS:
        raise OwnershipError(f"review_interval_days must be between 0 and {MAX_REVIEW_INTERVAL_DAYS}")
    return n


def effective_intervals(suites: Iterable[Any]) -> Dict[int, int]:
    """suite id -> effective review interval, for a whole project's suites at
    once (same inheritance rule as test_service._execution_flags)."""
    by_id = {s.id: s for s in suites}
    memo: Dict[int, Optional[int]] = {}

    def own(s) -> Optional[int]:
        settings = s.settings if isinstance(s.settings, dict) else {}
        try:
            return normalize_interval(settings.get("review_interval_days"))
        except OwnershipError:
            return None

    def resolve(sid: int, depth: int = 0) -> Optional[int]:
        if sid in memo:
            return memo[sid]
        s = by_id.get(sid)
        if s is None or depth > 100:
            return None
        value = own(s)
        if value is None and s.inherit_settings and s.parent_id:
            value = resolve(s.parent_id, depth + 1)
        memo[sid] = value
        return value

    return {sid: (v if v is not None else DEFAULT_REVIEW_INTERVAL_DAYS)
            for sid, v in ((sid, resolve(sid)) for sid in by_id)}


def review_status(last_reviewed_at: Optional[datetime], interval_days: int,
                  now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.utcnow()
    if interval_days == 0:
        return {"status": "not_required", "due_at": None}
    if last_reviewed_at is None:
        return {"status": "never", "due_at": None}
    due = last_reviewed_at + timedelta(days=interval_days)
    return {"status": "overdue" if due <= now else "ok", "due_at": due}


def owner_state(user: Any, has_access: bool) -> str:
    """'ok' | 'no_access' (deactivated, erased or no longer on the project)."""
    if user is None or not getattr(user, "is_active", True) or not has_access:
        return "no_access"
    return "ok"


def person(user: Any, has_access: bool) -> Optional[Dict[str, Any]]:
    if user is None:
        return None
    state = owner_state(user, has_access)
    return {"id": user.id, "name": user.full_name or user.email, "email": user.email,
            "state": state}


# --- first failure after a pass (owner notification) -------------------------

# Results that say nothing about the test itself never notify an owner.
_NOT_A_TEST_FAILURE = {"infrastructure", "cancelled", "not_executed", "timeout"}


def is_test_failure(status: Any, error_kind: Optional[str]) -> bool:
    name = getattr(status, "value", status)
    name = str(name or "").upper()
    if name not in ("FAILED", "ERROR"):
        return False
    return (error_kind or "") not in _NOT_A_TEST_FAILURE
