"""Capture-policy resolution.

Two layers, and the interaction between them is the whole point:

  - a *project* declares what it wants to capture (`Project.data_policy`);
  - the *instance* declares a ceiling nobody may exceed (`MAX_CAPTURE_LEVEL`).

The ceiling is what makes a scoped deployment defensible. An operator running
TraceIQ against anything near a cardholder data environment sets the instance
to `minimal`, and from that point no project — however misconfigured, however
determined the user — can cause a HAR or a trace to be captured. That is an
assertion an auditor can verify once, at the instance level, instead of
re-verifying every project forever.

So the tests that matter most here are the ones proving the ceiling cannot be
escaped, including by junk input.
"""
from app.services.data_policy import (
    CAPTURE_LEVELS,
    DEFAULT_CAPTURE_LEVEL,
    cap_capture_level,
    effective_data_policy,
    normalize_capture_level,
)


# --------------------------------------------------------------------------
# Level normalisation
# --------------------------------------------------------------------------

def test_known_levels_pass_through():
    for level in CAPTURE_LEVELS:
        assert normalize_capture_level(level) == level


def test_levels_are_case_and_whitespace_insensitive():
    assert normalize_capture_level("  FULL ") == "full"


def test_unknown_level_falls_back_to_standard_not_full():
    # Failing open would turn a typo into a disclosure.
    assert normalize_capture_level("everything") == DEFAULT_CAPTURE_LEVEL
    assert normalize_capture_level(None) == DEFAULT_CAPTURE_LEVEL
    assert normalize_capture_level("") == DEFAULT_CAPTURE_LEVEL
    assert normalize_capture_level(42) == DEFAULT_CAPTURE_LEVEL


def test_default_is_standard_not_full():
    # The pre-policy behaviour was effectively `full`. New projects must not
    # inherit that.
    assert DEFAULT_CAPTURE_LEVEL == "standard"


def test_levels_are_ordered_least_to_most_revealing():
    assert CAPTURE_LEVELS == ("none", "minimal", "standard", "full")


# --------------------------------------------------------------------------
# The instance ceiling
# --------------------------------------------------------------------------

def test_request_below_the_ceiling_is_granted():
    assert cap_capture_level("minimal", "full") == "minimal"


def test_request_above_the_ceiling_is_clamped():
    assert cap_capture_level("full", "minimal") == "minimal"


def test_request_equal_to_the_ceiling_is_granted():
    assert cap_capture_level("standard", "standard") == "standard"


def test_a_none_ceiling_suppresses_everything():
    for level in CAPTURE_LEVELS:
        assert cap_capture_level(level, "none") == "none"


def test_an_unset_ceiling_does_not_mean_unlimited():
    # An operator who has not configured a ceiling gets the default, not `full`.
    assert cap_capture_level("full", None) == DEFAULT_CAPTURE_LEVEL
    assert cap_capture_level("full", "") == DEFAULT_CAPTURE_LEVEL


def test_a_garbage_ceiling_does_not_escalate():
    # A typo in an instance setting must not silently lift the ceiling.
    assert cap_capture_level("full", "unlimited") == DEFAULT_CAPTURE_LEVEL


def test_a_garbage_request_under_a_low_ceiling_stays_clamped():
    assert cap_capture_level("nonsense", "minimal") == "minimal"


# --------------------------------------------------------------------------
# Effective policy
# --------------------------------------------------------------------------

def test_absent_project_policy_yields_the_defaults():
    policy = effective_data_policy(None, ceiling="full")
    assert policy["capture_level"] == DEFAULT_CAPTURE_LEVEL
    assert policy["redact_headers"] == []
    assert policy["mask_selectors"] == []


def test_project_policy_fields_are_carried_through():
    policy = effective_data_policy({
        "capture_level": "full",
        "redact_headers": ["x-tenant"],
        "redact_body_fields": ["policy_no"],
        "redact_patterns": ["pan", "email"],
        "mask_selectors": ["#ssn"],
    }, ceiling="full")
    assert policy["capture_level"] == "full"
    assert policy["redact_headers"] == ["x-tenant"]
    assert policy["redact_body_fields"] == ["policy_no"]
    assert policy["redact_patterns"] == ["pan", "email"]
    assert policy["mask_selectors"] == ["#ssn"]


def test_effective_policy_clamps_the_project_request():
    policy = effective_data_policy({"capture_level": "full"}, ceiling="minimal")
    assert policy["capture_level"] == "minimal"


def test_unknown_project_keys_are_dropped():
    # The blob is user-editable JSON; only known keys reach the worker.
    policy = effective_data_policy({"capture_level": "none", "evil": True}, ceiling="full")
    assert "evil" not in policy


def test_redact_patterns_none_means_use_defaults_not_none_at_all():
    # Distinct from []: None -> built-in patterns, [] -> scan for nothing.
    assert effective_data_policy({}, ceiling="full")["redact_patterns"] is None
    assert effective_data_policy({"redact_patterns": []}, ceiling="full")["redact_patterns"] == []


def test_non_list_redaction_fields_are_coerced_to_empty():
    policy = effective_data_policy(
        {"redact_headers": "x-tenant", "mask_selectors": 5}, ceiling="full")
    assert policy["redact_headers"] == []
    assert policy["mask_selectors"] == []


def test_a_non_dict_project_policy_is_tolerated():
    for junk in ("nonsense", [], 7, True):
        assert effective_data_policy(junk, ceiling="full")["capture_level"] == DEFAULT_CAPTURE_LEVEL


def test_store_bodies_defaults_true_and_is_respected():
    assert effective_data_policy({}, ceiling="full")["store_bodies"] is True
    assert effective_data_policy({"store_bodies": False}, ceiling="full")["store_bodies"] is False
