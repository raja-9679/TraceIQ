"""The capture policy must actually reach the worker.

Everything else in workstream B is inert if the resolved policy is not on the
dispatched job: the worker falls back to its own defaults, and the instance
ceiling — the control an auditor verifies — stops being enforced at all.

This tests the seam between `_load_data_policy` (which applies the ceiling) and
`_settings_payload` (which serialises the job), without needing Redis or a
browser.
"""
from app.services.data_policy import effective_data_policy
from app.worker import _settings_payload


def test_settings_payload_carries_the_data_policy():
    policy = effective_data_policy({"capture_level": "minimal"}, ceiling="full")
    payload = _settings_payload({"data_policy": policy})
    assert payload["data_policy"]["capture_level"] == "minimal"


def test_settings_payload_carries_redaction_hints():
    policy = effective_data_policy({
        "redact_headers": ["x-tenant"],
        "redact_body_fields": ["policy_no"],
        "mask_selectors": ["#ssn"],
    }, ceiling="full")
    payload = _settings_payload({"data_policy": policy})
    assert payload["data_policy"]["redact_headers"] == ["x-tenant"]
    assert payload["data_policy"]["redact_body_fields"] == ["policy_no"]
    assert payload["data_policy"]["mask_selectors"] == ["#ssn"]


def test_a_clamped_policy_is_what_reaches_the_worker():
    # The worker must never see the project's unclamped request — it has no
    # way to know the instance ceiling, so enforcement has to happen here.
    policy = effective_data_policy({"capture_level": "full"}, ceiling="minimal")
    payload = _settings_payload({"data_policy": policy})
    assert payload["data_policy"]["capture_level"] == "minimal"


def test_har_capture_and_data_policy_coexist():
    # har_capture is an older suite-level flag. It survives, but the capture
    # level still governs whether a HAR is actually retained.
    payload = _settings_payload({
        "har_capture": True,
        "data_policy": effective_data_policy({"capture_level": "standard"}, ceiling="full"),
    })
    assert payload["har_capture"] is True
    assert payload["data_policy"]["capture_level"] == "standard"


def test_settings_payload_still_works_without_a_policy():
    # Jobs dispatched by an older backend, or replayed from an old queue.
    payload = _settings_payload({})
    assert "data_policy" not in payload
    assert payload["headers"] == {}


def test_settings_payload_does_not_leak_unknown_policy_keys():
    policy = effective_data_policy({"capture_level": "none", "evil": True}, ceiling="full")
    payload = _settings_payload({"data_policy": policy})
    assert "evil" not in payload["data_policy"]
