"""PagerDuty / Opsgenie wire formats and maintenance windows (WP8).

HTTP is mocked at `paging._post`, the module's single network call.

    ./run-tests.sh tests/test_paging.py -q
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from app.services import paging


def test_pagerduty_trigger_and_resolve_payloads():
    body = paging.pagerduty_payload("RK", "trigger", "traceiq:monitor:5:eu",
                                    summary="down", severity="critical",
                                    details={"run_id": 9}, link="https://t/runs/9")
    assert body["routing_key"] == "RK"
    assert body["event_action"] == "trigger"
    assert body["dedup_key"] == "traceiq:monitor:5:eu"
    assert body["payload"]["severity"] == "critical"
    assert body["payload"]["custom_details"] == {"run_id": 9}
    assert body["links"][0]["href"] == "https://t/runs/9"
    resolve = paging.pagerduty_payload("RK", "resolve", "traceiq:monitor:5:eu", summary="ok")
    assert resolve == {"routing_key": "RK", "event_action": "resolve",
                       "dedup_key": "traceiq:monitor:5:eu"}
    assert paging.pagerduty_payload("RK", "trigger", "k", summary="x",
                                    severity="bogus")["payload"]["severity"] == "error"


def test_opsgenie_create_and_close_requests():
    req = paging.opsgenie_request("KEY", "trigger", "traceiq:run:1:2", summary="x" * 200,
                                  severity="warning", api_region="eu")
    assert req["url"] == "https://api.eu.opsgenie.com/v2/alerts"
    assert req["headers"]["Authorization"] == "GenieKey KEY"
    assert len(req["json"]["message"]) == 130
    assert req["json"]["alias"] == "traceiq:run:1:2"
    assert req["json"]["priority"] == "P3"
    close = paging.opsgenie_request("KEY", "resolve", "traceiq:run:1:2", summary="ok")
    assert close["url"] == ("https://api.opsgenie.com/v2/alerts/traceiq%3Arun%3A1%3A2"
                            "/close?identifierType=alias")


def _window(**kw):
    base = dict(enabled=True, starts_at=None, ends_at=None, weekdays=None,
                start_time=None, end_time=None, timezone="UTC", name="w", id=1)
    return SimpleNamespace(**{**base, **kw})


def test_one_off_window():
    w = _window(starts_at=datetime(2026, 10, 15, 10), ends_at=datetime(2026, 10, 15, 12))
    assert paging.window_active(w, datetime(2026, 10, 15, 11))
    assert not paging.window_active(w, datetime(2026, 10, 15, 12))
    assert not paging.window_active(_window(**{**w.__dict__, "enabled": False}),
                                    datetime(2026, 10, 15, 11))


def test_weekly_window_in_its_own_timezone():
    # Sundays 02:00-04:00 Berlin. 2026-10-18 is a Sunday; Berlin is UTC+2 (CEST).
    w = _window(weekdays=[6], start_time="02:00", end_time="04:00", timezone="Europe/Berlin")
    assert paging.window_active(w, datetime(2026, 10, 18, 0, 30))     # 02:30 local
    assert not paging.window_active(w, datetime(2026, 10, 18, 2, 30))  # 04:30 local
    # After the DST switch Berlin is UTC+1: same local window, new UTC hours.
    # 2026-11-01 is a Sunday.
    assert paging.window_active(w, datetime(2026, 11, 1, 1, 30))     # 02:30 CET
    assert not paging.window_active(w, datetime(2026, 11, 1, 0, 30))  # 01:30 CET


def test_overnight_window_belongs_to_its_start_day():
    # Friday 22:00 -> 06:00 (wraps into Saturday). 2026-10-16 is a Friday.
    w = _window(weekdays=[4], start_time="22:00", end_time="06:00")
    assert paging.window_active(w, datetime(2026, 10, 16, 23, 0))
    assert paging.window_active(w, datetime(2026, 10, 17, 5, 59))     # Saturday morning
    assert not paging.window_active(w, datetime(2026, 10, 17, 23, 0))  # Saturday night
    assert not paging.window_active(w, datetime(2026, 10, 16, 5, 0))  # Friday morning


def test_window_validation():
    ok = dict(starts_at=None, ends_at=None, weekdays=[0], start_time="01:00",
              end_time="02:00", timezone="UTC")
    paging.validate_window(**ok)
    for bad in (
        {**ok, "timezone": "Mars/Base"},
        {**ok, "weekdays": [7]},
        {**ok, "start_time": "25:00"},
        {**ok, "end_time": "01:00"},
        {**ok, "starts_at": datetime(2026, 1, 1), "ends_at": datetime(2026, 1, 2)},   # both kinds
        dict(starts_at=datetime(2026, 1, 2), ends_at=datetime(2026, 1, 1), weekdays=None,
             start_time=None, end_time=None, timezone="UTC"),
    ):
        with pytest.raises(paging.WindowError):
            paging.validate_window(**bad)


class _Resp:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text


def test_send_routes_to_the_right_vendor(monkeypatch):
    sent = []
    monkeypatch.setattr(paging, "_post", lambda url, json, headers=None:
                        sent.append((url, json, headers)) or _Resp(202))
    pd = SimpleNamespace(kind="pagerduty", severity="error", api_region=None)
    og = SimpleNamespace(kind="opsgenie", severity="critical", api_region="us")
    paging.send(pd, "RK", "trigger", "k1", summary="s")
    paging.send(og, "GK", "resolve", "k1", summary="s")
    assert sent[0][0] == paging.PAGERDUTY_EVENTS_URL and sent[0][1]["routing_key"] == "RK"
    assert sent[1][0].endswith("/k1/close?identifierType=alias")
    assert sent[1][2]["Authorization"] == "GenieKey GK"


def test_send_raises_on_vendor_error(monkeypatch):
    monkeypatch.setattr(paging, "_post", lambda *a, **k: _Resp(400, "Invalid routing key"))
    with pytest.raises(RuntimeError, match="HTTP 400"):
        paging.send(SimpleNamespace(kind="pagerduty", severity="error", api_region=None),
                    "RK", "trigger", "k", summary="s")


def test_dedup_keys():
    assert paging.monitor_dedup_key(5, None) == "traceiq:monitor:5"
    assert paging.monitor_dedup_key(5, "eu") == "traceiq:monitor:5:eu"
    assert paging.run_dedup_key(1, 2, None) == "traceiq:run:1:2"
    assert paging.run_dedup_key(1, 2, 3, "ap") == "traceiq:run:1:2:3@ap"
