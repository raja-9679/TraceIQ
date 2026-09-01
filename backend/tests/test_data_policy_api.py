"""Validation and presentation for the per-project data policy.

The policy blob was enforceable before this but not settable: no endpoint read
or wrote it, so the only way to configure a project was direct SQL. These are
the pure pieces behind the API — validation of an incoming payload, and the
read model the UI renders.

The read model deliberately carries BOTH what the project asked for and what it
actually gets after the instance ceiling is applied. A UI that displayed
`capture_level: full` while `MAX_CAPTURE_LEVEL=standard` silently discarded
video and traces would be lying to the person configuring it, which is worse
than not having the screen at all.
"""
import pytest

from app.services.data_policy import (
    AVAILABLE_PATTERNS,
    CAPTURE_LEVELS,
    describe_for_project,
    validate_data_policy,
)


class _Project:
    def __init__(self, data_policy=None):
        self.data_policy = data_policy


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def test_a_full_valid_payload_is_accepted():
    out = validate_data_policy({
        "capture_level": "minimal",
        "store_bodies": False,
        "redact_headers": ["x-tenant"],
        "redact_body_fields": ["policy_no"],
        "redact_patterns": ["pan", "email"],
        "mask_selectors": ["#ssn"],
        "retention_days": 30,
    })
    assert out["capture_level"] == "minimal"
    assert out["store_bodies"] is False
    assert out["redact_patterns"] == ["pan", "email"]
    assert out["retention_days"] == 30


def test_an_empty_payload_is_accepted_and_changes_nothing():
    assert validate_data_policy({}) == {}


def test_omitted_fields_are_absent_rather_than_defaulted():
    # A partial PUT must not silently reset the fields it did not mention —
    # that is how someone editing the retention window turns off redaction.
    out = validate_data_policy({"retention_days": 7})
    assert out == {"retention_days": 7}


def test_an_unknown_capture_level_is_rejected():
    with pytest.raises(ValueError, match="capture_level"):
        validate_data_policy({"capture_level": "everything"})


def test_capture_level_is_normalised():
    assert validate_data_policy({"capture_level": " FULL "})["capture_level"] == "full"


def test_every_documented_level_is_accepted():
    for level in CAPTURE_LEVELS:
        assert validate_data_policy({"capture_level": level})["capture_level"] == level


def test_an_unknown_redaction_pattern_is_rejected():
    # A typo here would silently scan for nothing, which is the failure mode
    # you would never notice.
    with pytest.raises(ValueError, match="redact_patterns"):
        validate_data_policy({"redact_patterns": ["pan", "rot13"]})


def test_every_documented_pattern_is_accepted():
    out = validate_data_policy({"redact_patterns": list(AVAILABLE_PATTERNS)})
    assert out["redact_patterns"] == list(AVAILABLE_PATTERNS)


def test_an_empty_pattern_list_is_allowed_and_distinct_from_omitting_it():
    # [] means "scan for nothing"; omitting means "use the built-in set".
    assert validate_data_policy({"redact_patterns": []})["redact_patterns"] == []
    assert "redact_patterns" not in validate_data_policy({})


def test_a_scalar_where_a_list_belongs_is_rejected():
    with pytest.raises(ValueError, match="redact_headers"):
        validate_data_policy({"redact_headers": "x-tenant"})


def test_non_string_list_entries_are_rejected():
    with pytest.raises(ValueError, match="mask_selectors"):
        validate_data_policy({"mask_selectors": ["#ok", 42]})


def test_blank_list_entries_are_dropped_not_rejected():
    out = validate_data_policy({"redact_headers": ["x-a", "  ", ""]})
    assert out["redact_headers"] == ["x-a"]


def test_negative_retention_is_rejected():
    with pytest.raises(ValueError, match="retention_days"):
        validate_data_policy({"retention_days": -1})


def test_non_integer_retention_is_rejected():
    with pytest.raises(ValueError, match="retention_days"):
        validate_data_policy({"retention_days": "thirty"})


def test_unknown_keys_are_rejected_rather_than_silently_dropped():
    # Silently dropping a misspelled key makes the UI look like it saved
    # something it did not.
    with pytest.raises(ValueError, match="unknown"):
        validate_data_policy({"capture_levl": "full"})


def test_a_non_dict_payload_is_rejected():
    with pytest.raises(ValueError):
        validate_data_policy(["full"])


# --------------------------------------------------------------------------
# Read model
# --------------------------------------------------------------------------

def test_describe_reports_defaults_for_a_project_with_no_policy():
    out = describe_for_project(_Project(None), ceiling="full")
    assert out["stored"] is None
    assert out["effective"]["capture_level"] == "standard"
    assert out["clamped"] is False


def test_describe_returns_the_stored_blob_verbatim():
    stored = {"capture_level": "full", "mask_selectors": ["#ssn"]}
    out = describe_for_project(_Project(stored), ceiling="full")
    assert out["stored"] == stored


def test_describe_flags_a_clamped_policy():
    # The project asked for full; the instance caps at standard.
    out = describe_for_project(_Project({"capture_level": "full"}), ceiling="standard")
    assert out["effective"]["capture_level"] == "standard"
    assert out["instance_max_capture_level"] == "standard"
    assert out["clamped"] is True


def test_describe_does_not_flag_clamping_when_the_request_is_within_the_ceiling():
    out = describe_for_project(_Project({"capture_level": "minimal"}), ceiling="full")
    assert out["clamped"] is False


def test_describe_advertises_the_available_choices():
    out = describe_for_project(_Project(None), ceiling="full")
    assert out["available_capture_levels"] == list(CAPTURE_LEVELS)
    assert out["available_patterns"] == list(AVAILABLE_PATTERNS)


def test_describe_marks_which_kinds_the_effective_level_permits():
    # This is what lets the UI explain *why* video is off, rather than just
    # leaving the user to wonder.
    standard = describe_for_project(_Project({"capture_level": "standard"}), ceiling="full")
    assert standard["permits"]["screenshot"] is True
    assert standard["permits"]["video"] is False
    assert standard["permits"]["trace"] is False
    assert standard["permits"]["har"] is False
    full = describe_for_project(_Project({"capture_level": "full"}), ceiling="full")
    assert all(full["permits"].values())


def test_describe_permits_nothing_at_level_none():
    out = describe_for_project(_Project({"capture_level": "none"}), ceiling="full")
    assert not any(out["permits"].values())
