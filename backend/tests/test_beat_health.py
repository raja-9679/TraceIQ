"""Detecting a dead celery_beat — workstream H1.

There is exactly one `celery_beat` with no leader election, and it drains
`jobs:results` every two seconds. If it dies, the entire execution pipeline
stalls: no finalization, no result aggregation, no schedules, no retention. Runs
sit at RUNNING forever.

The part that makes it a production hazard is not that it can die — anything can
die — it is that **nothing noticed**. No alert, no metric, no log anybody reads.
The first signal was a user asking why their run had been "running" for an hour.

Leader election (redbeat) lets you run two of them. This is the other half:
knowing. Beat writes a heartbeat on a schedule, and its absence is a number
something can alert on.

The staleness decision is pure so the boundaries are pinned:

* Never seen at all is *unknown*, not *dead* — an instance that has just started,
  or one deliberately running without beat, must not page anybody.
* A clock that has gone backwards must not read as healthy-forever.
"""
from datetime import datetime, timedelta

import pytest

from app.services.beat_health import BeatHealth, evaluate_beat_health

NOW = datetime(2026, 8, 10, 12, 0, 0)


def _iso(seconds_ago: int) -> str:
    return (NOW - timedelta(seconds=seconds_ago)).isoformat()


# --- Healthy ------------------------------------------------------------------

def test_a_recent_heartbeat_is_healthy():
    health = evaluate_beat_health(_iso(5), now=NOW, stale_after=180)
    assert health.state == "ok"
    assert health.age_seconds == 5


def test_a_heartbeat_just_inside_the_window_is_healthy():
    health = evaluate_beat_health(_iso(179), now=NOW, stale_after=180)
    assert health.state == "ok"


# --- Stale --------------------------------------------------------------------

def test_a_heartbeat_past_the_window_is_stale():
    health = evaluate_beat_health(_iso(600), now=NOW, stale_after=180)
    assert health.state == "stale"
    assert health.age_seconds == 600


def test_the_boundary_is_stale_not_healthy():
    # Ties go to "stale". A scheduler that is exactly one window behind is not
    # something to give the benefit of the doubt to.
    assert evaluate_beat_health(_iso(180), now=NOW, stale_after=180).state == "stale"


def test_a_stale_heartbeat_explains_the_consequence():
    # The message goes into an alert. "stale" alone does not tell an on-call
    # engineer that runs are silently not finalising.
    health = evaluate_beat_health(_iso(600), now=NOW, stale_after=180)
    assert "finali" in health.detail.lower() or "stall" in health.detail.lower()


# --- Unknown ------------------------------------------------------------------

def test_never_seen_is_unknown_not_dead():
    # A just-started instance, or a deployment deliberately running no beat,
    # must not page anybody.
    health = evaluate_beat_health(None, now=NOW, stale_after=180)
    assert health.state == "unknown"
    assert health.age_seconds is None


def test_an_unparseable_heartbeat_is_unknown():
    assert evaluate_beat_health("not-a-timestamp", now=NOW,
                               stale_after=180).state == "unknown"


def test_an_empty_heartbeat_is_unknown():
    assert evaluate_beat_health("", now=NOW, stale_after=180).state == "unknown"


# --- Clock weirdness ----------------------------------------------------------

def test_a_future_heartbeat_is_clamped_to_zero_not_negative():
    # Clock skew between beat and the API. A negative age would compare as
    # healthy forever, so it is clamped rather than trusted.
    health = evaluate_beat_health(_iso(-60), now=NOW, stale_after=180)
    assert health.age_seconds == 0
    assert health.state == "ok"


def test_a_wildly_future_heartbeat_is_reported_as_skewed():
    # An hour into the future is not skew, it is a misconfigured clock, and
    # silently treating it as healthy hides a real problem.
    health = evaluate_beat_health(_iso(-3600), now=NOW, stale_after=180)
    assert health.state == "skewed"


# --- Shape --------------------------------------------------------------------

def test_health_serialises_for_the_api():
    body = evaluate_beat_health(_iso(5), now=NOW, stale_after=180).as_dict()
    assert set(body) >= {"state", "age_seconds", "last_tick", "detail"}


@pytest.mark.parametrize("state", ["ok", "stale", "unknown", "skewed"])
def test_every_state_is_representable(state):
    assert BeatHealth(state=state, age_seconds=None, last_tick=None,
                      detail="x").state == state
