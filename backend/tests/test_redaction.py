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

from app.services.redaction import (
    REDACTED,
    RedactionPolicy,
    redact_body,
    redact_headers,
    redact_audit_changes,
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
