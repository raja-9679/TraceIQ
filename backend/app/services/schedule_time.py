"""Cron evaluation in a schedule's own timezone.

Schedules store `next_run_at` as naive UTC (like every other timestamp in the
schema); the cron expression is read as LOCAL wall-clock time in the schedule's
IANA timezone, so "0 9 * * 1" means 9 AM Monday where the team is.

croniter is only used to step through wall-clock times: given a tz-aware start
it mis-handles DST transitions (a 9 AM New York job fires at 10 AM on the
fall-back day). Each naive local candidate is converted with zoneinfo instead:

- a time skipped by a spring-forward gap (02:30 in New York on the change
  day) fires at the same offset after the gap (03:30 local), never dropped;
- a time repeated by a fall-back (01:30 twice) fires once, on the first
  occurrence.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

DEFAULT_TIMEZONE = "UTC"

# Cron fields can only match so far apart (Feb 29 on a Monday: years), but a
# valid expression always yields within a few candidates of the start; the cap
# only guards the loop against a pathological expression.
_MAX_CANDIDATES = 1000


def validate_timezone(name: Optional[str]) -> str:
    """Return a usable IANA name, or raise ValueError. None/'' → UTC."""
    if not name:
        return DEFAULT_TIMEZONE
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown timezone: {name}") from exc
    return name


def next_run_utc(cron_expression: str, tz_name: Optional[str],
                 after: Optional[datetime] = None) -> datetime:
    """Next time `cron_expression` fires in `tz_name`, strictly after `after`.

    `after` and the result are naive UTC. Raises ValueError for an unknown
    timezone or an expression with no upcoming match.
    """
    zone = ZoneInfo(validate_timezone(tz_name))
    after_utc = (after or datetime.utcnow()).replace(tzinfo=timezone.utc)
    # Start an hour early in wall-clock terms: after a fall-back the local
    # clock can read earlier than the previous candidate already did.
    start_local = after_utc.astimezone(zone).replace(tzinfo=None) - timedelta(hours=1)
    it = croniter(cron_expression, start_local)
    for _ in range(_MAX_CANDIDATES):
        local = it.get_next(datetime)
        # fold=0: first occurrence of a repeated time; for a skipped time it
        # applies the pre-gap offset, which lands the same distance past it.
        candidate = local.replace(tzinfo=zone, fold=0).astimezone(timezone.utc)
        if candidate > after_utc:
            return candidate.replace(tzinfo=None)
    raise ValueError(f"No upcoming run for cron expression: {cron_expression}")
