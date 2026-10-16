"""Suite retry policy as dispatched to the worker: bounded, and 0 means 0."""
import pytest

from app.worker import _settings_payload, retry_backoff_ms, retry_count


@pytest.mark.parametrize("raw,expected", [
    (None, 2), ("", 2), ("x", 2), (True, 2),   # junk -> default
    (0, 0),                                    # used to become 2
    (1, 1), (3, 3), ("4", 4), (5, 5),
    (6, 5), (1000, 5), (-1, 0),                # clamped
])
def test_retry_count(raw, expected):
    assert retry_count(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    (None, 1000), (0, 0), (2500, 2500), (10**9, 60_000), (-5, 0), ("abc", 1000),
])
def test_retry_backoff(raw, expected):
    assert retry_backoff_ms(raw) == expected


def test_payload_carries_bounded_retries_only_when_on():
    on = _settings_payload({"auto_retry": True, "max_retries": 99, "retry_backoff_ms": 10**8})
    assert (on["auto_retry"], on["max_retries"], on["retry_backoff_ms"]) == (True, 5, 60_000)
    for off in ({}, {"auto_retry": False, "max_retries": 3}, {"auto_retry": "yes"}):
        assert "auto_retry" not in _settings_payload(off)
        assert "max_retries" not in _settings_payload(off)
