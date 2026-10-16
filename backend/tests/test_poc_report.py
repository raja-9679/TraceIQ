"""WP13b PoC report: the time-saved estimate, input validation and the
public (share-link) payload allow-list."""
import json
from datetime import date

import pytest

from app.services import poc_report as poc


def test_default_assumption_is_five_minutes_and_always_labelled_an_estimate():
    a = poc.parse_assumptions()
    assert a == {"minutes_per_execution": 5.0, "hourly_rate": None, "currency": None}
    e = poc.estimate(120, a)
    assert e["hours_saved"] == 10.0
    assert e["label"].startswith("Estimated 10 hours")
    assert "120 automated case executions x 5 min" in e["label"]
    assert "not a measurement" in e["basis"]
    assert e["money"] is None


def test_zero_data():
    e = poc.estimate(0, poc.parse_assumptions())
    assert e["hours_saved"] == 0.0 and e["automated_executions"] == 0
    assert e["label"].startswith("Estimated 0 hours")
    assert poc.estimate(None, poc.parse_assumptions())["hours_saved"] == 0.0
    assert poc.estimate(-5, poc.parse_assumptions())["automated_executions"] == 0


def test_money_needs_a_rate_and_a_typed_currency():
    a = poc.parse_assumptions(minutes="7.5", hourly_rate="40", currency="eur")
    e = poc.estimate(80, a)
    assert e["hours_saved"] == 10.0
    assert e["money"]["amount"] == 400.0 and e["money"]["currency"] == "EUR"
    assert e["money"]["label"] == "Estimated EUR 400.00 at EUR 40.00 per hour"
    with pytest.raises(ValueError):
        poc.parse_assumptions(hourly_rate=40)              # rate without a currency
    for bad in ({"minutes": 0}, {"minutes": 481}, {"minutes": "x"}, {"hourly_rate": -1, "currency": "EUR"},
                {"hourly_rate": 10, "currency": "EURO"}, {"hourly_rate": 10, "currency": "$"}):
        with pytest.raises(ValueError):
            poc.parse_assumptions(**bad)


def test_range():
    today = date(2026, 10, 16)
    assert poc.parse_range(None, None, today) == (date(2026, 9, 17), today)
    assert poc.parse_range("2026-10-01", "2026-10-01", today) == (date(2026, 10, 1), date(2026, 10, 1))
    for s, e in (("2026-10-02", "2026-10-01"), ("2025-01-01", "2026-10-01"), ("yesterday", None)):
        with pytest.raises(ValueError):
            poc.parse_range(s, e, today)


def test_public_payload_is_an_allow_list():
    report = {
        "workspace_id": 9, "workspace_name": "Acme", "start": "2026-10-01", "end": "2026-10-14",
        "days": 14, "generated_at": "x", "project_ids": [1, 2],
        "inventory": {"projects": ["Shop"], "suites": 3, "cases": 10, "cases_by_executor": {"ui_playwright": 10},
                      "environments": ["staging"], "monitors": ["Checkout"],
                      "integrations": {"issue_trackers": ["jira"], "outbound_webhooks": 1, "incident_webhooks": 0,
                                       "paging_channels": 0, "status_pages": 1, "api_keys": 2,
                                       "webhook_urls": ["https://hooks.slack.com/services/T/B/secret"]},
                      "environment_urls": ["https://staging.internal"]},
        "activity": {"runs": 4, "passed_runs": 3, "pass_rate": 75.0, "case_executions": 40,
                     "manual_case_executions": 2, "browsers": ["chromium"], "devices": [],
                     "last_error": "Timeout waiting for #pay"},
        "value": {"failures_caught": 5, "failure_clusters": 2, "security_findings": {"high": 1},
                  "security_findings_total": 1, "flaky_tests": 1, "incidents": 1, "incidents_linked": 1,
                  "cluster_titles": ["Timeout waiting for #pay"], "owner": "dev@corp.example.com"},
        "assumptions": {"minutes_per_execution": 5.0, "hourly_rate": None, "currency": None},
        "estimate": poc.estimate(40, poc.parse_assumptions()),
    }
    out = poc.public_payload(report)
    dumped = json.dumps(out)
    for secret in ("hooks.slack.com", "staging.internal", "Timeout", "dev@corp", "project_ids", "workspace_id"):
        assert secret not in dumped
    assert out["kind"] == "poc_report"
    assert out["inventory"]["integrations"]["issue_trackers"] == ["jira"]
    assert out["value"]["security_findings"]["high"] == 1
    assert out["value"]["security_findings"]["critical"] is None
    assert out["estimate"]["hours_saved"] == pytest.approx(3.3)
    assert out["estimate"]["money"] is None
