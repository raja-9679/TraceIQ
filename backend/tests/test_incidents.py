"""Incident webhook auth, payload adapters, recurrence fingerprints, matching,
skeleton proposals and the escaped-defect rollup — the pure parts.

    ./run-tests.sh tests/test_incidents.py -q
"""
import hashlib
import hmac
import json
from datetime import datetime

import pytest

from app.services import incidents as svc
from app.services.generation_proposals import case_name, skeleton_steps

SECRET = "tiqwh_test_secret"
BODY = json.dumps({"title": "Checkout 500"}).encode()


def _hex(body=BODY, secret=SECRET):
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# --- HMAC ---------------------------------------------------------------

def test_traceiq_signature_with_and_without_prefix():
    assert svc.verify_request(SECRET, BODY, {"X-TraceIQ-Signature": f"sha256={_hex()}"})
    assert svc.verify_request(SECRET, BODY, {"x-traceiq-signature": _hex()})


def test_pagerduty_v1_signature_any_of_several():
    header = f"v1=deadbeef,v1={_hex()}"
    assert svc.verify_request(SECRET, BODY, {"X-PagerDuty-Signature": header})


def test_opsgenie_static_token_header():
    assert svc.verify_request(SECRET, BODY, {"X-TraceIQ-Token": SECRET})
    assert not svc.verify_request(SECRET, BODY, {"X-TraceIQ-Token": SECRET + "x"})


@pytest.mark.parametrize("headers", [
    {},
    {"X-TraceIQ-Signature": "sha256=" + "0" * 64},
    {"X-TraceIQ-Signature": f"sha256={_hex(secret='other')}"},
    {"X-PagerDuty-Signature": f"v0={_hex()}"},
    {"X-TraceIQ-Signature": f"sha256={_hex(body=b'{}')}"},      # signed a different body
])
def test_bad_signatures_are_refused(headers):
    assert not svc.verify_request(SECRET, BODY, headers)


def test_empty_secret_never_verifies():
    assert not svc.verify_request("", BODY, {"X-TraceIQ-Token": ""})


# --- adapters -------------------------------------------------------------

PD = {"event": {
    "id": "01DEN", "event_type": "incident.triggered", "resource_type": "incident",
    "occurred_at": "2026-10-10T10:00:05.000Z",
    "data": {"id": "PGR0VU2", "type": "incident", "number": 2,
             "title": "Checkout API returning 503", "status": "triggered", "urgency": "high",
             "priority": {"summary": "P1"}, "html_url": "https://acme.pagerduty.com/incidents/PGR0VU2",
             "service": {"id": "PF9KMXH", "summary": "checkout-api"},
             "created_at": "2026-10-10T10:00:00Z"}}}

OPSGENIE = {"action": "Create", "integrationName": "TraceIQ",
            "alert": {"alertId": "a1b2", "message": "Login latency above SLO",
                      "priority": "P3", "createdAt": 1760090400000, "entity": "auth-service",
                      "tags": ["service:auth"], "description": "p95 > 2s",
                      "details": {"url": "https://shop.example.com/login", "commit": "abc123"}}}


def test_source_detection():
    assert svc.detect_source(PD) == "pagerduty"
    assert svc.detect_source(OPSGENIE) == "opsgenie"
    assert svc.detect_source({"title": "x"}) == "generic"


def test_pagerduty_adapter():
    d = svc.normalise_payload(PD)
    assert d["source"] == "pagerduty" and d["external_id"] == "PGR0VU2"
    assert d["title"] == "Checkout API returning 503"
    assert d["severity"] == "critical"          # P1
    assert d["status"] == "open"
    assert d["service"] == "checkout-api"
    assert d["url"].startswith("https://acme.pagerduty.com/")
    assert d["started_at"] == datetime(2026, 10, 10, 10, 0)
    assert d["resolved_at"] is None


def test_pagerduty_resolved_and_urgency_fallback():
    ev = json.loads(json.dumps(PD))
    ev["event"]["event_type"] = "incident.resolved"
    ev["event"]["data"]["status"] = "resolved"
    ev["event"]["data"]["priority"] = None
    d = svc.normalise_payload(ev)
    assert d["status"] == "resolved" and d["resolved_at"] == datetime(2026, 10, 10, 10, 0, 5)
    assert d["severity"] == "high"              # urgency high, no priority


def test_opsgenie_adapter():
    d = svc.normalise_payload(OPSGENIE)
    assert (d["source"], d["external_id"], d["severity"], d["service"]) == \
        ("opsgenie", "a1b2", "medium", "auth-service")
    assert d["affected_url"] == "https://shop.example.com/login"
    assert d["git_commit"] == "abc123"
    assert d["started_at"] == datetime(2025, 10, 10, 10, 0)
    closed = svc.normalise_payload({**OPSGENIE, "action": "Close"})
    assert closed["status"] == "resolved"


def test_generic_adapter_and_unsafe_urls():
    d = svc.normalise_payload({"title": "Search down", "severity": "sev2", "service": "search",
                               "url": "javascript:alert(1)", "affected_url": "https://x.example/search",
                               "started_at": "2026-10-11T09:00:00+02:00", "git_branch": "main",
                               "external_id": 77})
    assert d["severity"] == "high" and d["url"] is None
    assert d["started_at"] == datetime(2026, 10, 11, 7, 0)
    assert d["external_id"] == "77" and d["git_branch"] == "main"


def test_explicit_source_overrides_detection_and_title_required():
    with pytest.raises(svc.IncidentPayloadError):
        svc.normalise_payload({"severity": "p1"})
    with pytest.raises(svc.IncidentPayloadError):
        svc.normalise_payload([1, 2])
    with pytest.raises(svc.IncidentPayloadError):
        svc.normalise_payload({"title": "x"}, source="nagios")


@pytest.mark.parametrize("raw,want", [("P1", "critical"), ("SEV-2", "high"), ("warning", "medium"),
                                      ("minor", "low"), ("", "unknown"), ("weird", "unknown")])
def test_severity_table(raw, want):
    assert svc.normalise_severity(raw) == want


# --- fingerprint ----------------------------------------------------------

def test_fingerprint_ignores_numbers_ids_and_timestamps():
    a = svc.fingerprint("checkout-api", "Checkout API 503 for 12% of requests at 10:42")
    b = svc.fingerprint("Checkout-API ", "checkout api 502 for 40% of requests at 2026-10-11 09:00")
    c = svc.fingerprint("checkout-api", "[FIRING:1] Checkout API 503 for 3% of requests at 11:02 (9f8e7d6c5b)")
    assert a == b == c


def test_fingerprint_distinguishes_service_and_meaning():
    base = svc.fingerprint("checkout-api", "Checkout API returning 503")
    assert svc.fingerprint("search-api", "Checkout API returning 503") != base
    assert svc.fingerprint("checkout-api", "Checkout page blank") != base


def test_normalise_title_strips_uuid_ip_hex():
    t = svc.normalise_title("DB 10.0.0.12:5432 unreachable for tenant 3f2c1a7e-1d2b-4c5d-8e9f-0a1b2c3d4e5f")
    assert t == "db unreachable for tenant"


# --- matching ---------------------------------------------------------------

def test_match_prefers_url_over_keywords():
    cases = [(1, "checkout guest payment flow", []),
             (2, "unrelated", [{"type": "goto", "value": "https://shop.example.com/cart/"}])]
    m = svc.match_existing("Checkout payment failing", "https://shop.example.com/cart?x=1", [], cases)
    assert m["case_id"] == 2 and "URL" in m["reason"]


def test_match_by_keywords_needs_two_shared_words():
    cases = [(1, "Checkout payment with UPI", []), (2, "Checkout banner", [])]
    clusters = [(9, "Timeout waiting for #pay-button", "payment button timeout on checkout")]
    m = svc.match_existing("Checkout payment timeout", None, clusters, cases)
    assert m["case_id"] == 1 and m["cluster_id"] == 9
    assert svc.match_existing("Payment slow", None, clusters, cases)["case_id"] is None


# --- skeletons ----------------------------------------------------------------

def test_skeleton_from_gherkin_maps_clauses():
    steps = skeleton_steps("Scenario: pay\nGiven a cart\nWhen I pay\nAnd confirm\nThen I see the receipt\nAnd an email",
                           "https://shop.example.com")
    assert [s["type"] for s in steps] == ["goto", "expect-visible", "click", "click", "expect-text", "expect-text"]
    assert all(s.get("selector", "x") == "" for s in steps[1:])
    assert steps[2]["intent"] == "TODO: When I pay"


def test_skeleton_for_plain_criterion_is_one_unfinished_assertion():
    [step] = skeleton_steps("The footer shows the version")
    assert step["type"] == "expect-text" and step["selector"] == ""
    assert "footer shows the version" in step["intent"]


def test_case_name_strips_scenario_prefix():
    assert case_name("SHOP-1 AC2:", "Scenario: guest pays\nGiven x") == "SHOP-1 AC2: guest pays"


# --- escaped rollup -------------------------------------------------------------

class _I:
    def __init__(self, started, case=None, no_test=False, reg=None):
        self.started_at, self.escaped_case_id, self.escaped_no_test = started, case, no_test
        self.regression_case_id = reg


def test_escaped_rollup_by_month():
    from app.api.incidents import escaped_rollup
    rows = [
        (_I(datetime(2026, 9, 3), case=5, reg=7), datetime(2026, 9, 4)),    # test added after
        (_I(datetime(2026, 9, 20), no_test=True, reg=8), datetime(2026, 8, 1)),  # pre-existing case linked
        (_I(datetime(2026, 10, 1), no_test=True), None),
    ]
    out = escaped_rollup(rows, "month")
    assert [p["period"] for p in out["periods"]] == ["2026-09", "2026-10"]
    sep = out["periods"][0]
    assert (sep["escaped"], sep["missed_by_existing_test"], sep["no_test_existed"], sep["test_added_after"]) == (2, 1, 1, 1)
    assert sep["pct_test_added_after"] == 50.0
    assert out["totals"]["escaped"] == 3 and out["totals"]["pct_test_added_after"] == 33.3


def test_escaped_rollup_by_week_and_empty():
    from app.api.incidents import escaped_rollup
    out = escaped_rollup([(_I(datetime(2026, 10, 15)), None)], "week")
    assert out["periods"][0]["period"] == "2026-10-12"
    assert escaped_rollup([], "month")["totals"]["pct_test_added_after"] is None
