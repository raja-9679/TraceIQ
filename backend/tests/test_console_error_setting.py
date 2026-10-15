"""fail_on_console_errors: suite-chain inheritance, dispatch and the case override."""
from types import SimpleNamespace

from app.services.test_service import _execution_flags, _merge_settings
from app.worker import _case_payload, _settings_payload, console_ignore_patterns


def test_child_inherits_flags_it_does_not_set():
    parent = {"fail_on_console_errors": True, "console_error_ignore_patterns": ["favicon"], "har_capture": True}
    merged = _merge_settings(parent, {"headers": {"a": "1"}})
    assert merged["fail_on_console_errors"] is True
    assert merged["console_error_ignore_patterns"] == ["favicon"]
    # har_capture used to be dropped by the merge and never reached a job.
    assert merged["har_capture"] is True


def test_child_overrides_flags_it_sets():
    merged = _merge_settings({"fail_on_console_errors": True, "console_error_ignore_patterns": ["a"]},
                             {"fail_on_console_errors": False, "console_error_ignore_patterns": ["b"]})
    assert merged["fail_on_console_errors"] is False
    assert merged["console_error_ignore_patterns"] == ["b"]


def test_unset_flags_stay_absent():
    assert _execution_flags({}, {"headers": {}}) == {}
    assert "fail_on_console_errors" not in _merge_settings({}, {})


def test_dispatch_carries_the_setting_only_when_on():
    assert "fail_on_console_errors" not in _settings_payload({})
    assert "fail_on_console_errors" not in _settings_payload({"fail_on_console_errors": "yes"})
    payload = _settings_payload({"fail_on_console_errors": True,
                                 "console_error_ignore_patterns": [" favicon ", "", 7, "x" * 500]})
    assert payload["fail_on_console_errors"] is True
    assert payload["console_error_ignore_patterns"] == ["favicon", "x" * 200]


def test_ignore_patterns_are_capped():
    assert len(console_ignore_patterns([f"p{i}" for i in range(80)])) == 50
    assert console_ignore_patterns("favicon") == []


def _case(**kw):
    base = dict(id=1, name="c", executor=None, raw_script=None, steps=[], is_auth_setup=False,
                use_auth_session=True)
    base.update(kw)
    return SimpleNamespace(**base)


def test_case_override_rides_on_the_case_payload():
    assert "fail_on_console_errors" not in _case_payload(_case())
    assert "fail_on_console_errors" not in _case_payload(_case(fail_on_console_errors=None))
    assert _case_payload(_case(fail_on_console_errors=False))["fail_on_console_errors"] is False
    assert _case_payload(_case(fail_on_console_errors=True))["fail_on_console_errors"] is True
