"""Cron evaluation in a schedule's timezone (app/services/schedule_time.py).

The DST cases are the reason the helper exists: croniter given a tz-aware
start fires a 9 AM New York job at 10 AM on the fall-back day and produces
non-existent local times on the spring-forward day.
"""
from datetime import datetime

import pytest

from app.services.schedule_time import next_run_utc, validate_timezone


def _runs(expr, tz, after, n):
    out = []
    for _ in range(n):
        after = next_run_utc(expr, tz, after=after)
        out.append(after)
    return out


def test_utc_matches_plain_cron():
    assert next_run_utc("0 9 * * 1", "UTC", after=datetime(2026, 10, 16, 6)) == \
        datetime(2026, 10, 19, 9)


def test_ist_monday_nine_am_is_three_thirty_utc():
    assert next_run_utc("0 9 * * 1", "Asia/Kolkata", after=datetime(2026, 10, 16)) == \
        datetime(2026, 10, 19, 3, 30)


def test_result_is_strictly_after():
    # Exactly at a firing time: the next one, not the same one.
    assert next_run_utc("0 9 * * *", "UTC", after=datetime(2026, 10, 16, 9)) == \
        datetime(2026, 10, 17, 9)


def test_new_york_nine_am_stays_nine_am_across_fall_back():
    # DST ends Sun 2026-11-01: EDT (-4) → EST (-5).
    assert _runs("0 9 * * *", "America/New_York", datetime(2026, 10, 31, 12), 3) == [
        datetime(2026, 10, 31, 13),   # 09:00 EDT
        datetime(2026, 11, 1, 14),    # 09:00 EST  (croniter alone says 15:00)
        datetime(2026, 11, 2, 14),
    ]


def test_skipped_time_on_spring_forward_fires_after_the_gap():
    # 2026-03-08 02:00 EST jumps to 03:00 EDT; 02:30 does not exist that day.
    assert _runs("30 2 * * *", "America/New_York", datetime(2026, 3, 7, 12), 3) == [
        datetime(2026, 3, 8, 7, 30),  # 03:30 EDT, the same distance past the gap
        datetime(2026, 3, 9, 6, 30),  # 02:30 EDT
        datetime(2026, 3, 10, 6, 30),
    ]


def test_repeated_time_on_fall_back_fires_once():
    # 01:30 happens twice on 2026-11-01 (EDT then EST): fire on the first only.
    assert _runs("30 1 * * *", "America/New_York", datetime(2026, 10, 31, 12), 3) == [
        datetime(2026, 11, 1, 5, 30),  # 01:30 EDT
        datetime(2026, 11, 2, 6, 30),  # 01:30 EST (croniter alone says 02:30)
        datetime(2026, 11, 3, 6, 30),
    ]


def test_hourly_across_fall_back_never_goes_backwards_or_stalls():
    runs = _runs("0 * * * *", "Europe/London", datetime(2026, 10, 24, 22), 8)
    assert runs == sorted(set(runs))
    assert all((b - a).total_seconds() <= 2 * 3600 for a, b in zip(runs, runs[1:]))


def test_called_inside_the_repeated_hour_moves_forward():
    # 06:15Z on 2026-11-01 is 01:15 EST, the SECOND 01:15. The next 01:30 first
    # occurrence (05:30Z) is in the past; the result must not be.
    assert next_run_utc("30 1 * * *", "America/New_York",
                        after=datetime(2026, 11, 1, 6, 15)) == datetime(2026, 11, 2, 6, 30)


@pytest.mark.parametrize("name", [None, ""])
def test_missing_timezone_means_utc(name):
    assert validate_timezone(name) == "UTC"
    assert next_run_utc("0 9 * * *", name, after=datetime(2026, 10, 16)) == \
        datetime(2026, 10, 16, 9)


@pytest.mark.parametrize("name", ["Mars/Olympus", "IST", "../etc/passwd", "UTC+5:30"])
def test_unknown_timezone_is_refused(name):
    with pytest.raises(ValueError):
        validate_timezone(name)
