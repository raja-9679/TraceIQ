"""Redaction corpus.

Nothing in this file is hypothetical: every payload shape here is something the
execution worker actually captures and the backend actually persists into
`TestCaseResult.request_body` / `.response_body` / `.request_headers` /
`.response_headers` and `TestRun.network_events`.

The two failure modes are symmetric and both matter:
  - under-redaction leaks a credential or a PAN into an unencrypted column;
  - over-redaction destroys the debugging value of a failed run, which is the
    entire point of capturing this data.

So the false-positive tests are as load-bearing as the true-positive ones.
"""
import json

import pytest

from app.services.redaction import (
    REDACTED,
    RedactionPolicy,
    redact_body,
    redact_headers,
    redact_message_text,
    redact_audit_changes,
    redact_hook_report,
    redact_steps,
    redact_text,
    redact_worker_result,
)

# A card number that passes Luhn (the canonical Visa test number).
VALID_PAN = "4111111111111111"
# Same length, deliberately fails Luhn — an order id, not a card.
NOT_A_PAN = "1234567890123456"
# 12 digits with a correct Verhoeff check digit.
VALID_AADHAAR = "234123412346"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"


# --------------------------------------------------------------------------
# Headers
# --------------------------------------------------------------------------

def test_authorization_header_is_redacted():
    out = redact_headers({"Authorization": "Bearer abc.def.ghi"})
    assert out["Authorization"] == REDACTED


def test_header_matching_is_case_insensitive():
    out = redact_headers({"AUTHORIZATION": "x", "set-cookie": "s=1", "X-Api-Key": "k"})
    assert out["AUTHORIZATION"] == REDACTED
    assert out["set-cookie"] == REDACTED
    assert out["X-Api-Key"] == REDACTED


def test_benign_headers_survive_untouched():
    out = redact_headers({"Content-Type": "application/json", "X-Request-Id": "req-42"})
    assert out == {"Content-Type": "application/json", "X-Request-Id": "req-42"}


def test_header_names_are_preserved_so_the_shape_stays_debuggable():
    out = redact_headers({"Cookie": "session=abc"})
    assert list(out.keys()) == ["Cookie"]


def test_policy_can_add_extra_header_names():
    policy = RedactionPolicy(header_names=("x-tenant-token",))
    out = redact_headers({"X-Tenant-Token": "t"}, policy)
    assert out["X-Tenant-Token"] == REDACTED


def test_non_dict_headers_do_not_explode():
    assert redact_headers(None) is None
    assert redact_headers([]) == []


# --------------------------------------------------------------------------
# JSON bodies — key denylist
# --------------------------------------------------------------------------

def test_password_field_is_redacted_by_key_name():
    out = redact_body(json.dumps({"email": "a@b.com", "password": "hunter2"}))
    assert json.loads(out)["password"] == REDACTED


def test_key_denylist_is_case_and_separator_insensitive():
    body = json.dumps({"API_KEY": "1", "apiKey": "2", "api-key": "3", "Access_Token": "4"})
    out = json.loads(redact_body(body))
    assert all(v == REDACTED for v in out.values())


def test_nested_objects_are_redacted():
    body = json.dumps({"user": {"profile": {"ssn": "123-45-6789"}}})
    assert json.loads(redact_body(body))["user"]["profile"]["ssn"] == REDACTED


def test_arrays_of_objects_are_redacted():
    body = json.dumps({"cards": [{"cvv": "123"}, {"cvv": "456"}]})
    out = json.loads(redact_body(body))
    assert [c["cvv"] for c in out["cards"]] == [REDACTED, REDACTED]


def test_non_sensitive_fields_are_preserved_exactly():
    payload = {"orderId": 991, "status": "shipped", "items": ["a", "b"], "total": 12.5}
    assert json.loads(redact_body(json.dumps(payload))) == payload


def test_json_structure_and_types_are_preserved():
    body = json.dumps({"count": 3, "ok": True, "nothing": None, "password": "x"})
    out = json.loads(redact_body(body))
    assert out["count"] == 3 and out["ok"] is True and out["nothing"] is None


# --------------------------------------------------------------------------
# Value patterns
# --------------------------------------------------------------------------

def test_luhn_valid_card_number_is_redacted_even_under_an_innocuous_key():
    out = redact_body(json.dumps({"note": f"charged {VALID_PAN} today"}))
    assert VALID_PAN not in out
    assert "[REDACTED:pan]" in out


def test_card_number_with_spaces_is_redacted():
    out = redact_body(json.dumps({"note": "4111 1111 1111 1111"}))
    assert "4111" not in out


def test_number_that_fails_luhn_is_left_alone():
    # Over-redaction destroys debuggability; a 16-digit order id is not a card.
    out = redact_body(json.dumps({"orderRef": NOT_A_PAN}))
    assert NOT_A_PAN in out


def test_aadhaar_with_valid_checksum_is_redacted():
    out = redact_body(json.dumps({"note": f"id {VALID_AADHAAR}"}))
    assert VALID_AADHAAR not in out


def test_twelve_digits_failing_verhoeff_are_left_alone():
    not_aadhaar = "234123412341"
    assert not_aadhaar in redact_body(json.dumps({"ref": not_aadhaar}))


def test_jwt_is_redacted_wherever_it_appears():
    out = redact_body(json.dumps({"debug": f"token={JWT}"}))
    assert JWT not in out
    assert "[REDACTED:jwt]" in out


# --- cloud keys (WP7) ---------------------------------------------------------
# Built by concatenation so the literal never appears in the repo: GitHub push
# protection and secret scanners would (rightly) flag a contiguous token.
AWS_KEY_ID = "AKIA" + "IOSFODNN7EXAMPLE"
GH_TOKEN = "ghp" + "_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
SLACK_TOKEN = "xox" + "b-" + "123456789012-abcdefghijkl"
STRIPE_KEY = "sk" + "_live_" + "4eC39HqLyjWDarjtT1zdp7dc"
GOOGLE_KEY = "AIza" + "SyA1234567890abcdefghijklmnopqrstuv"
AZURE_KEY = "A" * 86 + "=="
PEM = ("-----BEGIN RSA " + "PRIVATE KEY-----\nMIIEowIBAAKCAQEA7\n"
       "-----END RSA " + "PRIVATE KEY-----")


@pytest.mark.parametrize("secret,name", [
    (AWS_KEY_ID, "aws_access_key"),
    (GH_TOKEN, "github_token"),
    (SLACK_TOKEN, "slack_token"),
    (STRIPE_KEY, "stripe_key"),
    (GOOGLE_KEY, "google_api_key"),
])
def test_cloud_keys_are_redacted_by_default(secret, name):
    out = redact_body(json.dumps({"note": f"key is {secret} ok"}))
    assert secret not in out
    assert f"[REDACTED:{name}]" in out


def test_azure_storage_key_in_a_connection_string_is_redacted():
    conn = f"DefaultEndpointsProtocol=https;AccountName=x;AccountKey={AZURE_KEY};"
    out = redact_text(conn)
    assert AZURE_KEY not in out
    assert "AccountKey=[REDACTED:azure_storage_key];" in out


def test_pem_private_key_block_is_redacted_whole():
    out = redact_text(f"cfg {PEM} tail")
    assert "MIIEow" not in out
    assert out == "cfg [REDACTED:private_key] tail"


def test_truncated_pem_private_key_is_still_redacted():
    out = redact_text("-----BEGIN " + "PRIVATE KEY-----\nMIIEvQIBADANBgkq")
    assert out == "[REDACTED:private_key]"


def test_aws_key_digits_are_not_eaten_by_the_pan_rule_first():
    # Cloud keys run before PAN: the key is redacted as one token.
    out = redact_text("AKIA" + "4111111111111111")
    assert out == "[REDACTED:aws_access_key]"


def test_cloud_key_lookalikes_are_left_alone():
    for text in ("AKIA-not-a-key", "ghp_short", "sk_test_4eC39HqLyjWDarjtT1zdp7dc",
                 "xoxb-short", "AIzaShort"):
        assert redact_text(text) == text


def test_cloud_keys_can_be_switched_off_by_policy():
    policy = RedactionPolicy(patterns=("jwt",))
    assert redact_text(AWS_KEY_ID, policy) == AWS_KEY_ID


def test_email_is_kept_by_default():
    # Emails are ordinary test-fixture data; redacting them by default would
    # break most assertions. Insurance/health deployments opt in via policy.
    assert "a@b.com" in redact_body(json.dumps({"to": "a@b.com"}))


def test_email_is_redacted_when_the_policy_opts_in():
    policy = RedactionPolicy(patterns=("email",))
    assert "a@b.com" not in redact_body(json.dumps({"to": "a@b.com"}), policy=policy)


def test_policy_can_disable_all_value_patterns():
    policy = RedactionPolicy(patterns=())
    assert VALID_PAN in redact_body(json.dumps({"note": VALID_PAN}), policy=policy)


# --------------------------------------------------------------------------
# Non-JSON bodies
# --------------------------------------------------------------------------

def test_form_encoded_body_is_redacted_by_key():
    out = redact_body("username=alice&password=hunter2", content_type="application/x-www-form-urlencoded")
    assert "hunter2" not in out
    assert "username=alice" in out


def test_plain_text_body_still_gets_a_pattern_sweep():
    out = redact_body(f"card was {VALID_PAN}", content_type="text/plain")
    assert VALID_PAN not in out


def test_malformed_json_falls_back_to_a_text_sweep_rather_than_throwing():
    out = redact_body('{"password": "hunter2", ', content_type="application/json")
    assert isinstance(out, str)
    assert VALID_PAN not in out


def test_html_response_body_is_not_mangled():
    html = "<html><body><h1>Order 1234567890123456</h1></body></html>"
    assert redact_body(html, content_type="text/html") == html


def test_empty_and_none_bodies_are_passed_through():
    assert redact_body(None) is None
    assert redact_body("") == ""


def test_already_parsed_dict_body_is_supported():
    out = redact_body({"password": "x", "id": 1})
    assert out["password"] == REDACTED and out["id"] == 1


# --------------------------------------------------------------------------
# Free text
# --------------------------------------------------------------------------

def test_redact_text_sweeps_patterns():
    assert VALID_PAN not in redact_text(f"failed charging {VALID_PAN}")


def test_redact_text_leaves_ordinary_error_messages_intact():
    msg = 'Expected text "Welcome back" not found in element "#greeting"'
    assert redact_text(msg) == msg


def test_redact_text_handles_none():
    assert redact_text(None) is None


# --------------------------------------------------------------------------
# Idempotence — results get re-processed on retry, so this must be stable
# --------------------------------------------------------------------------

def test_redaction_is_idempotent():
    body = json.dumps({"password": "x", "note": VALID_PAN, "jwt": JWT})
    once = redact_body(body)
    assert redact_body(once) == once


# --------------------------------------------------------------------------
# Step arrays — what gets copied into AuditLog.changes
# --------------------------------------------------------------------------

def test_value_typed_into_a_password_field_is_redacted():
    steps = [{"type": "fill", "selector": "#password", "value": "hunter2"}]
    assert redact_steps(steps)[0]["value"] == REDACTED


def test_password_input_type_selector_is_recognised():
    steps = [{"type": "fill", "selector": 'input[type="password"]', "value": "hunter2"}]
    assert redact_steps(steps)[0]["value"] == REDACTED


def test_cvv_and_api_key_selectors_are_recognised():
    steps = [
        {"type": "fill", "selector": "[name=cvv]", "value": "123"},
        {"type": "fill", "selector": "#api_key", "value": "tiq_live_x"},
    ]
    out = redact_steps(steps)
    assert out[0]["value"] == REDACTED and out[1]["value"] == REDACTED


def test_ordinary_field_values_are_preserved():
    # Redacting every fill would make the audit log useless.
    steps = [{"type": "fill", "selector": "#search", "value": "kerala floods"}]
    assert redact_steps(steps)[0]["value"] == "kerala floods"


def test_secret_template_tokens_are_left_intact():
    # The token is not a secret — the value lives in ProjectSecret. Redacting
    # it would hide the fact that the case is correctly parameterised.
    steps = [{"type": "fill", "selector": "#password", "value": "{{secret.PASSWORD}}"}]
    assert redact_steps(steps)[0]["value"] == "{{secret.PASSWORD}}"


def test_card_number_in_an_ordinary_field_is_still_pattern_redacted():
    steps = [{"type": "fill", "selector": "#note", "value": f"card {VALID_PAN}"}]
    assert VALID_PAN not in redact_steps(steps)[0]["value"]


def test_step_params_are_redacted():
    steps = [{"type": "http-request", "value": "/login",
              "params": {"headers": {"Authorization": "Bearer x"}, "body": {"password": "p"}}}]
    out = redact_steps(steps)[0]["params"]
    assert out["headers"]["Authorization"] == REDACTED
    assert out["body"]["password"] == REDACTED


def test_step_type_and_selector_are_never_altered():
    steps = [{"type": "fill", "selector": "#password", "value": "x"}]
    out = redact_steps(steps)[0]
    assert out["type"] == "fill" and out["selector"] == "#password"


def test_redact_steps_tolerates_junk():
    assert redact_steps(None) is None
    assert redact_steps([]) == []
    assert redact_steps(["not-a-dict"]) == ["not-a-dict"]


# --------------------------------------------------------------------------
# AuditLog.changes
# --------------------------------------------------------------------------

def test_audit_changes_redacts_the_steps_array():
    changes = {
        "name": "Login journey",
        "steps": [{"type": "fill", "selector": "#password", "value": "hunter2"}],
    }
    out = redact_audit_changes(changes)
    assert out["steps"][0]["value"] == REDACTED
    assert out["name"] == "Login journey"


def test_audit_changes_redacts_sensitive_top_level_keys():
    assert redact_audit_changes({"api_key": "tiq_x"})["api_key"] == REDACTED


def test_audit_changes_without_steps_is_untouched():
    changes = {"name": {"from": "A", "to": "B"}, "priority": 2}
    assert redact_audit_changes(changes) == changes


def test_audit_changes_tolerates_non_dict():
    assert redact_audit_changes(None) is None
    assert redact_audit_changes([]) == []


# --------------------------------------------------------------------------
# Worker results on ingestion — the backend must not trust the worker image
# --------------------------------------------------------------------------

def _worker_payload():
    return {
        "job_id": "j-1",
        "run_id": 7,
        "status": "failed",
        "duration": 900,
        "error": f"declined {VALID_PAN}",
        "request_headers": {"Authorization": "Bearer tok", "Accept": "application/json"},
        "response_headers": {"Set-Cookie": "sid=abc"},
        "request_body": json.dumps({"cvv": "123", "amount": 10}),
        "response_body": json.dumps({"card": VALID_PAN, "ok": False}),
        "network_events": [{"url": "https://x/y", "requestHeaders": {"cookie": "a=b"}}],
        "test_results": [
            {"test_case_id": 3, "status": "failed", "error": f"pan {VALID_PAN}",
             "response_headers": {"set-cookie": "s=1"}},
        ],
    }


def test_worker_result_redacts_top_level_headers_and_bodies():
    out = redact_worker_result(_worker_payload())
    assert out["request_headers"]["Authorization"] == REDACTED
    assert out["request_headers"]["Accept"] == "application/json"
    assert out["response_headers"]["Set-Cookie"] == REDACTED
    assert json.loads(out["request_body"])["cvv"] == REDACTED
    assert VALID_PAN not in out["response_body"]


def test_worker_result_redacts_nested_test_results():
    out = redact_worker_result(_worker_payload())
    assert VALID_PAN not in out["test_results"][0]["error"]
    assert out["test_results"][0]["response_headers"]["set-cookie"] == REDACTED


def test_worker_result_redacts_network_events_and_error():
    out = redact_worker_result(_worker_payload())
    assert out["network_events"][0]["requestHeaders"]["cookie"] == REDACTED
    assert VALID_PAN not in out["error"]


def test_worker_result_redacts_each_retry_attempt_error():
    out = redact_worker_result({
        **_worker_payload(),
        "attempts": [{"attempt": 1, "status": "failed", "error": f"card {VALID_PAN} declined"},
                     {"attempt": 2, "status": "passed"}],
    })
    assert VALID_PAN not in out["attempts"][0]["error"]
    assert out["attempts"][1]["status"] == "passed"


def test_worker_result_redacts_per_step_evidence():
    out = redact_worker_result({
        **_worker_payload(),
        "step_evidence": [{
            "id": "s0", "index": 0, "type": "fill", "status": "failed", "duration_ms": 12,
            "error": f"card {VALID_PAN} rejected",
            "selector": f"#card-{VALID_PAN}",
            "console": [{"id": "s0.c0", "type": "error", "text": f"token {JWT}"}],
            "requests": [{"id": "s0.r0", "method": "GET", "url": f"https://x.test/reset/{JWT}", "status": 500}],
            "dom": f'<div data-pan="{VALID_PAN}">aadhaar {VALID_AADHAAR}</div>',
            "screenshot": "runs/7/screenshots/j-1-failure.png",
        }],
    })
    e = out["step_evidence"][0]
    flat = json.dumps(e)
    assert VALID_PAN not in flat
    assert JWT not in flat
    assert VALID_AADHAAR not in flat
    # Ids, status, status codes and artifact keys are routing data — untouched.
    assert e["id"] == "s0"
    assert e["requests"][0]["id"] == "s0.r0"
    assert e["requests"][0]["status"] == 500
    assert e["screenshot"] == "runs/7/screenshots/j-1-failure.png"


def test_per_step_evidence_is_redacted_inside_continuous_test_results_too():
    out = redact_worker_result({
        "job_id": "j-3", "run_id": 9, "status": "failed",
        "test_results": [{"test_case_id": 1, "status": "failed",
                          "step_evidence": [{"id": "s0", "console": [
                              {"id": "s0.c0", "type": "error", "text": f"pan {VALID_PAN}"}]}]}],
    })
    assert VALID_PAN not in json.dumps(out["test_results"][0]["step_evidence"])


def test_worker_result_preserves_routing_and_status():
    # These drive aggregation and finalization; losing one loses a result.
    out = redact_worker_result(_worker_payload())
    assert out["job_id"] == "j-1"
    assert out["run_id"] == 7
    assert out["status"] == "failed"
    assert out["duration"] == 900
    assert out["test_results"][0]["test_case_id"] == 3
    assert out["test_results"][0]["status"] == "failed"


def test_worker_result_handles_the_legacy_results_key():
    payload = {"run_id": 1, "results": [{"test_case_id": 1, "response_body": '{"password":"p"}'}]}
    out = redact_worker_result(payload)
    assert json.loads(out["results"][0]["response_body"])["password"] == REDACTED


def test_worker_result_tolerates_a_minimal_payload():
    minimal = {"job_id": "j", "run_id": 1, "status": "passed"}
    assert redact_worker_result(minimal) == minimal


def test_worker_result_tolerates_junk():
    assert redact_worker_result(None) is None
    assert redact_worker_result("nonsense") == "nonsense"


def test_worker_result_redaction_is_idempotent():
    once = redact_worker_result(_worker_payload())
    assert redact_worker_result(once) == once


# --------------------------------------------------------------------------
# Inbound messages (test inbox previews, captured webhooks)
# Mirrors the redactMessageText block in execution-engine/src/core/redact.test.ts.
# --------------------------------------------------------------------------

def test_message_reset_link_loses_query_string_and_opaque_path_token():
    assert redact_message_text(
        "Reset: https://shop.example.com/reset/9f8a7b6c5d4e3f2a1b0c9d8e?token=abc&x=1 now"
    ) == "Reset: https://shop.example.com/reset/[REDACTED:link]?[REDACTED:link] now"


def test_message_fragments_are_dropped_too():
    assert redact_message_text("go https://x.io/cb#access=abc") == "go https://x.io/cb#[REDACTED:link]"


def test_message_readable_paths_survive():
    text = "See https://shop.example.com/blog/announcing-the-new-dashboard today"
    assert redact_message_text(text) == text


def test_message_standalone_code_is_masked_even_before_a_full_stop():
    assert redact_message_text("Your code is 482913.") == "Your code is [REDACTED:code]."
    assert redact_message_text("PIN 12345678 expires") == "PIN [REDACTED:code] expires"


def test_message_years_decimals_and_alphanumeric_ids_are_not_codes():
    text = "In 2026 the total was 1234567.89 for order A1234567"
    assert redact_message_text(text) == text


def test_message_regular_value_patterns_still_apply():
    assert redact_message_text(f"card {VALID_PAN} jwt {JWT}") == "card [REDACTED:pan] jwt [REDACTED:jwt]"


def test_message_non_strings_pass_through():
    assert redact_message_text("") == ""
    assert redact_message_text(None) is None
# --- access-matrix observations (twin: redact.test.ts "an access observation …")

def test_access_observation_headers_body_and_urls_are_scrubbed():
    from app.services.redaction import redact_access_observation
    obs = redact_access_observation({
        "status": 200, "element_present": None,
        "headers": {"set-cookie": "sid=abc", "content-type": "application/json"},
        "body_excerpt": json.dumps({"token": "abc", "id": 7}),
        "url": f"https://shop.example.com/pay?card={VALID_PAN}",
        "swapped_id": "101",
    })
    assert obs["headers"]["set-cookie"] == REDACTED
    assert obs["headers"]["content-type"] == "application/json"
    assert json.loads(obs["body_excerpt"])["token"] == REDACTED
    assert json.loads(obs["body_excerpt"])["id"] == 7
    assert VALID_PAN not in obs["url"]
    assert obs["status"] == 200
    assert obs["swapped_id"] == "101"


def test_access_observation_truncated_excerpt_degrades_to_text_sweep():
    from app.services.redaction import redact_access_observation
    obs = redact_access_observation({"body_excerpt": f'{{"note": "{VALID_PAN}", "x": [1, 2'})
    assert VALID_PAN not in obs["body_excerpt"]


# --------------------------------------------------------------------------
# hook_report (suite setup/teardown + cleanup). Twin corpus:
# execution-engine/src/core/redact.test.ts "hook report: ..."
# --------------------------------------------------------------------------

def test_hook_report_cleanup_url_keeps_query_names_and_errors_are_scrubbed():
    out = redact_worker_result({
        "hook_report": {
            "setup": [{"suite_id": 1, "phase": "setup", "status": "failed",
                       "error": f"401 for token {JWT}",
                       "steps": [{"index": 0, "type": "http-request", "status": "failed",
                                  "error": f"bad {JWT}"}]}],
            "teardown": [],
            "cleanup": [{"method": "DELETE", "url": "https://api.test/items/9?token=s3cret&x=1",
                         "status": "failed", "error": "card 4111 1111 1111 1111 rejected",
                         "origin": "case"}],
        },
    })
    r = out["hook_report"]
    assert JWT not in r["setup"][0]["error"]
    assert JWT not in r["setup"][0]["steps"][0]["error"]
    assert r["cleanup"][0]["url"] == "https://api.test/items/9?token=\u2026&x=\u2026"
    assert "4111 1111 1111 1111" not in r["cleanup"][0]["error"]
    assert r["cleanup"][0]["status"] == "failed"
    assert r["setup"][0]["suite_id"] == 1


def test_hook_report_continuous_results_and_odd_shapes():
    out = redact_worker_result({"test_results": [{"hook_report": {"cleanup": [{"url": "/x?a=1#frag"}]}}]})
    assert out["test_results"][0]["hook_report"]["cleanup"][0]["url"] == "/x?a=\u2026"
    assert redact_hook_report(["x"]) == ["x"]
    out = redact_hook_report({"cleanup": [{"url": "{{api}}/items/{{id}}"}]})
    assert out["cleanup"][0]["url"] == "{{api}}/items/{{id}}"


# --- WP10: page budgets + crawl reports (twins in redact.test.ts) ---

def test_page_budget_outcomes_are_scrubbed_numbers_kept():
    from app.services.redaction import redact_page_budgets
    [out] = redact_page_budgets([{
        "step_id": "b1", "url": f"https://shop.example.com/reset/{JWT}", "passed": False,
        "failure": f"Page budget exceeded on https://shop.example.com/reset/{JWT}: lcp_ms 3000 > 2500",
        "metrics": {"lcp_ms": 3000},
        "checks": [{"metric": "lcp_ms", "value": 3000, "budget": 2500, "passed": False}],
        "seo": [{"check": "canonical", "passed": True,
                 "value": f"https://shop.example.com/reset/{JWT}", "detail": "ok"}],
    }])
    assert JWT not in json.dumps(out)
    assert out["metrics"]["lcp_ms"] == 3000
    assert out["checks"][0]["budget"] == 2500
    res = redact_worker_result({"page_budgets": [{"url": f"https://x.test/?t={JWT}"}]})
    assert JWT not in json.dumps(res)


def test_crawl_report_urls_and_redirect_hops_are_scrubbed():
    from app.services.redaction import redact_crawl_report
    out = redact_crawl_report({
        "status": "ok", "links_checked": 3,
        "pages": [{"url": f"https://s.example/p?card={VALID_PAN}", "status": 200}],
        "issues": [{
            "page_url": "https://s.example/", "target_url": f"https://s.example/reset/{JWT}",
            "category": "broken", "status_code": 404, "detail": f"see {JWT}",
            "redirect_chain": [{"url": f"https://s.example/r?token={JWT}", "status": 302}],
        }],
    })
    flat = json.dumps(out)
    assert JWT not in flat
    assert VALID_PAN not in flat
    assert out["issues"][0]["status_code"] == 404
    assert out["issues"][0]["redirect_chain"][0]["status"] == 302
    assert out["links_checked"] == 3


# --------------------------------------------------------------------------
# DOM snapshots for selector heal (twin: redact.test.ts "redactDom")
# --------------------------------------------------------------------------

from app.services.redaction import redact_dom  # noqa: E402

_DOM_FIXTURE = """<!DOCTYPE html><html><head>
<meta name="csrf-token" content="s3cr3t-csrf"><link rel="stylesheet" href="/a.css?v=1">
<script>window.API_KEY = "sk_live_abc123";</script><style>.x{color:red}</style></head>
<body><!-- build 4f2a debug token=xyz -->
<form id="login" action="/session?next=/home&amp;sig=abcdef">
  <input type="hidden" name="authenticity_token" value="tok-123">
  <input id="email" name="email" type="email" value="jane@corp.example" placeholder="Email">
  <input id="password" name="password" type="password" value="hunter2">
  <input type="submit" value="Sign in" onclick="steal()">
  <textarea name="notes">private notes</textarea>
  <button data-testid="login-submit" class="btn primary" aria-label="Log in" style="color:red">Log in</button>
  <img src="data:image/png;base64,AAAA" alt="logo">
  <div class="api-token-display">ghp_abcdefghijklmnopqrstuvwxyz0123456789</div>
  <span data-token="abc">Card 4111 1111 1111 1111</span>
  <div style="display: none" title="XLONG">Hidden panel</div>
  <div class="profile-card"><p>Jane Doe, 12 Main St</p></div>
</form></body></html>""".replace("XLONG", "x" * 300)


def test_redact_dom_strips_scripts_styles_comments_meta_and_hidden_inputs():
    out = redact_dom(_DOM_FIXTURE)
    for leaked in ("sk_live_abc123", "color:red}", "build 4f2a", "s3cr3t-csrf", "tok-123", "authenticity_token"):
        assert leaked not in out, leaked
    assert "<script></script>" in out


def test_redact_dom_drops_input_values_and_textarea_text_but_keeps_button_labels():
    out = redact_dom(_DOM_FIXTURE)
    assert "jane@corp.example" not in out
    assert "hunter2" not in out
    assert "private notes" not in out
    assert 'value="Sign in"' in out


def test_redact_dom_removes_text_in_credential_elements_and_secret_attributes():
    out = redact_dom(_DOM_FIXTURE)
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in out
    assert "data-token" not in out
    assert "4111 1111 1111 1111" not in out


def test_redact_dom_strips_handlers_data_uris_url_queries_and_long_attributes():
    out = redact_dom(_DOM_FIXTURE)
    assert "onclick" not in out
    assert "base64,AAAA" not in out
    assert 'src="data:[REDACTED]"' in out
    assert 'action="/session"' in out
    assert "xxxxxxxxxx" not in out
    assert 'style="display:none"' in out
    assert "color:red" not in out


def test_redact_dom_keeps_what_selectors_are_built_from():
    out = redact_dom(_DOM_FIXTURE)
    for kept in ('id="email"', 'name="email"', 'placeholder="Email"', 'data-testid="login-submit"',
                 'class="btn primary"', 'aria-label="Log in"', ">Log in</button>", 'id="password"',
                 'alt="logo"', "Hidden panel"):
        assert kept in out, kept


def test_redact_dom_applies_mask_selectors_and_policy_body_keys():
    out = redact_dom(_DOM_FIXTURE, RedactionPolicy(body_keys=("notes",)), mask_selectors=["div.profile-card"])
    assert "Jane Doe" not in out
    assert 'class="profile-card"' in out
    assert "Jane Doe" in redact_dom(_DOM_FIXTURE)


def test_redact_dom_is_idempotent_and_tolerates_junk():
    once = redact_dom(_DOM_FIXTURE)
    assert redact_dom(once) == once
    assert redact_dom("") == ""
    assert redact_dom(None) == ""
    assert redact_dom("<div <p>broken < 3 </div>")


def test_worker_result_heal_snapshots_are_redacted_as_a_backstop():
    out = redact_worker_result({
        "heal_suggestions": [{"dom_snapshot": '<input type="password" value="hunter2">'}],
        "step_dom": {"s1": '<script>secret()</script><input value="x1">'},
    })
    blob = str(out)
    assert "hunter2" not in blob
    assert "secret()" not in blob
