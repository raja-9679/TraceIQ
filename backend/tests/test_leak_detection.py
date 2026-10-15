"""Leak detection over stored run data (WP7, C12).

Synthetic credentials are assembled by concatenation so no contiguous token is
committed (push protection / secret scanners). This file is also allowlisted in
.gitleaks.toml alongside the redaction corpora.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services import leak_detection as ld

AWS = "AKIA" + "IOSFODNN7EXAMPLE"
GH = "ghp" + "_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
SLACK = "xox" + "b-" + "123456789012-abcdefghijkl"
STRIPE = "sk" + "_live_" + "4eC39HqLyjWDarjtT1zdp7dc"
PEM = "-----BEGIN RSA " + "PRIVATE KEY-----\nMIIEowIBAAKCAQEA7\n-----END RSA " + "PRIVATE KEY-----"
RANDOM_SECRET = "q8Zr2Lx0Vt7Nw3Ke9Hb4Pj6Ys1Gd5Fm0"


def run(**kw):
    base = dict(id=10, test_case_id=None, network_events=[], response_headers={},
                execution_log=[], test_suite_id=1)
    base.update(kw)
    return SimpleNamespace(**base)


def result(**kw):
    base = dict(id=100, test_case_id=5, request_url="https://app.example.com/api/me",
                response_body=None, response_headers={}, error_message=None, result_payload=None)
    base.update(kw)
    return SimpleNamespace(**base)


def detectors(findings):
    return sorted(f["detector"] for f in findings)


# --- positives ------------------------------------------------------------------

@pytest.mark.parametrize("secret,detector", [
    (AWS, "aws_access_key"), (GH, "github_token"), (SLACK, "slack_token"),
    (STRIPE, "stripe_key"), (PEM, "private_key"),
])
def test_raw_cloud_keys_in_a_response_body(secret, detector):
    body = json.dumps({"config": {"value": secret}})
    out = ld.scan_run(run(), [result(response_body=body)])
    assert detectors(out) == [detector]
    f = out[0]
    assert f["category"] == "sensitive_data_exposure"
    assert f["severity"] == "high"
    assert f["location"] == {"run_id": 10, "result_id": 100, "case_id": 5,
                             "url": "https://app.example.com/api/me",
                             "field": "response_body.config.value"}


def test_the_secret_itself_is_never_in_the_finding():
    out = ld.scan_run(run(), [result(response_body=json.dumps({"k": AWS, "g": GH}))])
    blob = json.dumps(out)
    assert AWS not in blob and GH not in blob
    assert any("AKIA…LE (20 chars)" in f["evidence"] for f in out)


def test_pem_sample_is_the_header_line_only():
    out = ld.scan_run(run(), [result(response_body=PEM)])
    assert "MIIEow" not in json.dumps(out)
    assert "-----BEGIN RSA PRIVATE KEY----- …" in out[0]["evidence"]


def test_redaction_marker_in_a_response_is_still_a_finding():
    # The worker masked it at capture — but the APP sent it.
    out = ld.scan_run(run(), [result(response_body='{"note":"[REDACTED:aws_access_key]"}')])
    assert detectors(out) == ["aws_access_key"]
    assert "masked at capture" in out[0]["evidence"]


def test_gcp_service_account_file_even_with_the_key_value_redacted():
    body = json.dumps({"type": "service_account", "project_id": "p",
                       "private_key_id": "[REDACTED]", "private_key": "[REDACTED]"})
    assert detectors(ld.scan_run(run(), [result(response_body=body)])) == ["gcp_service_account"]


def test_high_entropy_value_under_a_suspicious_key():
    out = ld.scan_run(run(), [result(response_body=json.dumps({"aws_secret_access_key": RANDOM_SECRET}))])
    assert detectors(out) == ["high_entropy_secret"]
    assert out[0]["severity"] == "medium"


def test_console_errors_network_events_and_urls_are_scanned():
    r = result(result_payload={"console_errors": [{"text": f"init failed with key {STRIPE}"}]})
    rn = run(network_events=[
        {"url": f"https://cdn.example.com/x?key={AWS}", "responseHeaders": {}},
        {"url": "https://api.example.com/cfg", "responseBody": json.dumps({"slack": SLACK})},
    ])
    out = ld.scan_run(rn, [r])
    fields = {f["location"]["field"] for f in out}
    assert "console_errors[0]" in fields
    assert "network_events[0].url" in fields
    assert "network_events[1].responseBody.slack" in fields
    titles = {f["title"] for f in out}
    assert "AWS access key ID exposed in a URL" in titles
    assert "Stripe live secret key exposed in console output" in titles


def test_email_and_phone_only_when_the_policy_marks_them_sensitive():
    body = json.dumps({"contact": "jane@corp.example", "mobile": "+91 98765 43210"})
    assert ld.scan_run(run(), [result(response_body=body)]) == []
    out = ld.scan_run(run(), [result(response_body=body)],
                      data_policy={"redact_patterns": ["pan", "email", "phone"]})
    assert detectors(out) == ["email", "phone"]
    assert all(f["severity"] == "low" for f in out)


def test_pii_markers_count_when_the_policy_marks_them_sensitive():
    body = '{"contact":"[REDACTED:email]"}'
    assert ld.scan_run(run(), [result(response_body=body)]) == []
    out = ld.scan_run(run(), [result(response_body=body)], data_policy={"redact_patterns": ["email"]})
    assert detectors(out) == ["email"]


# --- negatives ------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    json.dumps({"user": "jane", "id": 12345, "status": "ok"}),
    json.dumps({"csrfToken": RANDOM_SECRET}),                 # by design
    json.dumps({"nextPageToken": RANDOM_SECRET}),             # pagination
    json.dumps({"access_token": "[REDACTED]"}),               # plain key-name redaction
    json.dumps({"token": "[REDACTED:jwt]"}),                  # login responses carry JWTs
    json.dumps({"secret_question": "What was your first pet called"}),   # low entropy, spaces
    json.dumps({"api_key": "{{secret.API_KEY}}"}),            # a template, not a value
    "AKIA-not-a-key and ghp_short and sk_test_4eC39HqLyjWDarjtT1zdp7dc",
    "<html><body>Hello</body></html>",
])
def test_ordinary_responses_are_quiet(body):
    assert ld.scan_run(run(), [result(response_body=body)]) == []


def test_request_side_is_not_scanned():
    # What the TEST sent (its own login) is not the application leaking.
    r = result(response_body="{}")
    r.request_body = json.dumps({"password": RANDOM_SECRET, "key": AWS})
    r.request_headers = {"Authorization": f"token {GH}"}
    assert ld.scan_run(run(), [r]) == []


def test_a_typed_hit_is_not_also_reported_as_high_entropy():
    out = ld.scan_run(run(), [result(response_body=json.dumps({"github_token": GH}))])
    assert detectors(out) == ["github_token"]


# --- identity -------------------------------------------------------------------

def test_fingerprint_ignores_list_positions_but_not_the_field():
    a = ld.scan_run(run(network_events=[{}, {"url": "https://x/c", "responseBody": json.dumps([{"k": AWS}])}]), [])
    b = ld.scan_run(run(network_events=[{"url": "https://x/c", "responseBody": json.dumps([{}, {"k": AWS}])}]), [])
    assert a[0]["fingerprint"] == b[0]["fingerprint"]
    c = ld.scan_run(run(network_events=[{"url": "https://x/c", "responseBody": json.dumps({"other": AWS})}]), [])
    assert c[0]["fingerprint"] != a[0]["fingerprint"]


def test_duplicates_within_a_run_collapse_and_the_cap_holds(monkeypatch):
    rows = [result(id=i, response_body=json.dumps({"k": AWS})) for i in range(5)]
    assert len(ld.scan_run(run(), rows)) == 1
    monkeypatch.setattr(ld, "MAX_FINDINGS_PER_RUN", 3)
    many = [result(id=i, request_url=f"https://x/{i}", response_body=json.dumps({"k": AWS}))
            for i in range(10)]
    assert len(ld.scan_run(run(), many)) == 3


def test_entropy_helper():
    assert ld.shannon_entropy("aaaaaaaa") == 0
    assert ld.shannon_entropy(RANDOM_SECRET) > 4
