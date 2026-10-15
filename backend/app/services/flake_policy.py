"""Quarantine rules for flaky test cases — one place, used everywhere.

A quarantined case is skipped when a run is dispatched. Before 2026-10 a
quarantine lasted forever and the aggregator's score-based release could undo
a person's decision. Now:

* Every quarantine has an expiry. Past it the case runs again (the record
  still says quarantined, so the UI can show "expired — needs a decision"),
  but nothing re-hides it automatically: the aggregator only auto-quarantines
  a record that is not quarantined at all.
* Score-based release only undoes automatic quarantines (and legacy rows that
  predate `quarantine_source`), never a manual one.

Every query that asks "is this case quarantined right now?" must use
`active_quarantine_clause()` — a dispatch path that checked the bare flag
would keep an expired case hidden while another path ran it.
"""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import or_

from app.models import FlakeRecord

DEFAULT_QUARANTINE_DAYS = 14
MAX_QUARANTINE_DAYS = 90

QUARANTINE_SCORE = 0.4
QUARANTINE_MIN_SAMPLES = 6
RELEASE_SCORE = 0.15

SOURCE_AUTO = "auto"
SOURCE_MANUAL = "manual"


def active_quarantine_clause(now: Optional[datetime] = None):
    """SQL condition: the record's quarantine is in force at `now`.

    A NULL expiry counts as active: rows quarantined before the migration were
    backfilled with one, so NULL only appears if something writes the flag
    without going through `quarantine()`.
    """
    now = now or datetime.utcnow()
    return (FlakeRecord.is_quarantined == True) & or_(  # noqa: E712
        FlakeRecord.quarantine_expires_at.is_(None),
        FlakeRecord.quarantine_expires_at > now,
    )


def quarantine_state(record: FlakeRecord, now: Optional[datetime] = None) -> str:
    """'none' | 'active' | 'expired'."""
    if not record.is_quarantined:
        return "none"
    now = now or datetime.utcnow()
    if record.quarantine_expires_at is not None and record.quarantine_expires_at <= now:
        return "expired"
    return "active"


def is_active(record: Optional[FlakeRecord], now: Optional[datetime] = None) -> bool:
    return record is not None and quarantine_state(record, now) == "active"


def quarantine(record: FlakeRecord, *, source: str, days: int = DEFAULT_QUARANTINE_DAYS,
               reason: Optional[str] = None, by_user_id: Optional[int] = None,
               now: Optional[datetime] = None) -> None:
    if not 1 <= days <= MAX_QUARANTINE_DAYS:
        raise ValueError(f"quarantine length must be 1-{MAX_QUARANTINE_DAYS} days")
    now = now or datetime.utcnow()
    record.is_quarantined = True
    record.quarantine_source = source
    record.quarantine_reason = reason
    record.quarantined_by_id = by_user_id
    record.quarantined_at = now
    record.quarantine_expires_at = now + timedelta(days=days)


def release(record: FlakeRecord) -> None:
    """Clear the quarantine. The owner stays — it owns the flaky test, not the
    quarantine — and the history lives in the audit trail."""
    record.is_quarantined = False
    record.quarantine_source = None
    record.quarantine_reason = None
    record.quarantined_by_id = None
    record.quarantined_at = None
    record.quarantine_expires_at = None


def apply_score(record: FlakeRecord, score: float, samples: int,
                now: Optional[datetime] = None) -> Optional[str]:
    """Aggregator's automatic transition. Returns 'quarantined', 'released'
    or None."""
    if (score >= QUARANTINE_SCORE and samples >= QUARANTINE_MIN_SAMPLES
            and not record.is_quarantined):
        quarantine(record, source=SOURCE_AUTO,
                   reason=f"flake score {score:.2f} over {samples} runs", now=now)
        return "quarantined"
    if (score < RELEASE_SCORE and record.is_quarantined
            and record.quarantine_source != SOURCE_MANUAL):
        release(record)
        return "released"
    return None
