"""Access-control matrix: row validation, safety gates, verdicts, fan-out.

    ./run-tests.sh tests/test_access_matrix.py -q
"""
import pytest

from app.services import access_matrix as am
from app.services.access_matrix_runtime import build_cell_job, strip_identity

DENY = list(am.DEFAULT_DENY_STATUSES)


def _row(**kw):
    base = {"key": "r1", "kind": "request", "url": "https://app.example.com/api/x"}
    base.update(kw)
    return base


# --- verdicts ---------------------------------------------------------------

@pytest.mark.parametrize("status", [401, 403, 404])
def test_deny_passes_on_an_explicit_denial(status):
    assert am.judge_observation("request", "deny", {"status": status}, DENY)[0] == "pass"


def test_deny_fails_when_access_is_granted():
    verdict, reason = am.judge_observation("request", "deny", {"status": 200}, DENY)
    assert verdict == "fail" and "granted" in reason


@pytest.mark.parametrize("obs", [
    {"error_kind": "timeout", "error": "Timeout 30000ms exceeded"},
    {"error_kind": "network", "error": "ECONNREFUSED"},
    {"error_kind": "refused", "error": "not on allowlist"},
    {"error_kind": "extract_failed"},
    {"status": 500},
    {"status": 302},
    {},
    None,
])
def test_deny_is_inconclusive_unless_the_server_said_no(obs):
    assert am.judge_observation("request", "deny", obs, DENY)[0] == "inconclusive"


def test_deny_cell_status_extends_the_deny_set():
    assert am.judge_observation("request", "deny", {"status": 302}, DENY, expected_status=302)[0] == "pass"


def test_deny_set_rejects_success_codes():
    with pytest.raises(ValueError):
        am.deny_set([200, 403])
    assert am.deny_set(None) == [401, 403, 404]


def test_page_deny_on_missing_element_and_fail_on_visible_element():
    assert am.judge_observation("page", "deny", {"status": 200, "element_present": False},
                                DENY, has_selector=True)[0] == "pass"
    assert am.judge_observation("page", "deny", {"status": 200, "element_present": True},
                                DENY, has_selector=True)[0] == "fail"
    # A page that never answered is not "element absent".
    assert am.judge_observation("page", "deny", {"error_kind": "timeout"},
                                DENY, has_selector=True)[0] == "inconclusive"
    assert am.judge_observation("page", "deny", {"status": 500, "element_present": False},
                                DENY, has_selector=True)[0] == "inconclusive"


def test_allow_verdicts():
    assert am.judge_observation("request", "allow", {"status": 200}, DENY)[0] == "pass"
    assert am.judge_observation("request", "allow", {"status": 403}, DENY)[0] == "fail"
    assert am.judge_observation("request", "allow", {"status": 201}, DENY, expected_status=200)[0] == "fail"
    assert am.judge_observation("request", "allow", {"error_kind": "timeout"}, DENY)[0] == "inconclusive"
    assert am.judge_observation("page", "allow", {"status": 200, "element_present": False},
                                DENY, has_selector=True)[0] == "fail"


def test_case_row_verdicts():
    assert am.judge_case("deny", "passed", None, DENY)[0] == "fail"
    assert am.judge_case("deny", "failed", "Expected status 200 but got 403", DENY)[0] == "pass"
    assert am.judge_case("deny", "failed", "locator.click: Timeout 30000ms exceeded", DENY)[0] == "inconclusive"
    assert am.judge_case("allow", "passed", None, DENY)[0] == "pass"
    assert am.judge_case("allow", "failed", "boom", DENY)[0] == "fail"
    assert am.judge_case("allow", "running", None, DENY)[0] == "inconclusive"


# --- rows and fan-out -------------------------------------------------------

def test_parse_rows_validates_shape_and_columns():
    cols = am.column_keys([1, 2], include_anonymous=True)
    assert cols == ["1", "2", "anonymous"]
    rows = am.parse_rows([_row(cells={"1": {"expect": "allow"}, "anonymous": {"expect": "deny"}})], cols)
    assert rows[0].method == "GET"
    with pytest.raises(ValueError, match="unknown columns"):
        am.parse_rows([_row(cells={"9": {"expect": "deny"}})], cols)
    with pytest.raises(ValueError, match="duplicate"):
        am.parse_rows([_row(), _row()], cols)
    with pytest.raises(ValueError, match="test_case_id"):
        am.parse_rows([{"key": "c", "kind": "case"}], cols)
    with pytest.raises(ValueError, match="placeholder"):
        am.parse_rows([_row(swap={"owner_persona_id": 1, "url": "/mine",
                                  "extractor": {"expr": "0.id"}})], cols)
    with pytest.raises(ValueError, match="swap owner"):
        am.parse_rows([_row(url="/x/{id}", swap={"owner_persona_id": 7, "url": "/mine",
                                                  "extractor": {"expr": "0.id"}})], cols)


def test_plan_cells_skips_skip_and_absent_cells():
    cols = am.column_keys([1, 2], include_anonymous=True)
    rows = am.parse_rows([
        _row(key="a", cells={"1": {"expect": "allow"}, "2": {"expect": "deny"},
                             "anonymous": {"expect": "skip"}}),
        _row(key="b", cells={"anonymous": {"expect": "deny", "status": 302}}),
        _row(key="c"),
    ], cols)
    planned = am.plan_cells(rows, cols)
    assert [(r.key, col) for r, col, _ in planned] == [("a", "1"), ("a", "2"), ("b", "anonymous")]


# --- safety gates -----------------------------------------------------------

def _rows(*rows, cols=("1",)):
    return am.parse_rows(list(rows), list(cols))


def test_safety_requires_enabled_and_allowlisted_host():
    rows = _rows(_row(cells={"1": {"expect": "deny"}}))
    p = am.safety_problems(rows, None, security_enabled=False, allowed_domains=["app.example.com"],
                           authorized=False)
    assert p and p[0][0] == 403
    p = am.safety_problems(rows, None, security_enabled=True, allowed_domains=["other.com"],
                           authorized=False)
    assert p and "allowlist" in p[0][1]
    assert am.safety_problems(rows, None, security_enabled=True,
                              allowed_domains=["example.com"], authorized=False) == []


def test_safety_requires_attestation_for_mutations_including_swap_owner():
    rows = _rows(_row(method="DELETE", cells={"1": {"expect": "deny"}}))
    p = am.safety_problems(rows, None, security_enabled=True, allowed_domains=["example.com"],
                           authorized=False)
    assert p and "attest" in p[0][1]
    assert am.safety_problems(rows, None, security_enabled=True, allowed_domains=["example.com"],
                              authorized=True) == []
    swap = _rows(_row(url="/items/{id}", swap={"owner_persona_id": 1, "method": "POST", "url": "/items",
                                                "extractor": {"expr": "id"}},
                      cells={"1": {"expect": "allow"}}))
    p = am.safety_problems(swap, "https://app.example.com", security_enabled=True,
                           allowed_domains=["example.com"], authorized=False)
    assert any("POST" in msg for _, msg in p)


def test_safety_ignores_case_rows_and_all_skip_rows():
    rows = _rows({"key": "c", "kind": "case", "test_case_id": 5, "cells": {"1": {"expect": "deny"}}},
                 _row(key="parked", url="https://evil.example.org/", cells={"1": {"expect": "skip"}}))
    assert am.safety_problems(rows, None, security_enabled=False, allowed_domains=[],
                              authorized=False, columns=["1"]) == []


def test_relative_urls_need_base_url():
    rows = _rows(_row(url="/api/x", cells={"1": {"expect": "deny"}}))
    p = am.safety_problems(rows, None, security_enabled=True, allowed_domains=["example.com"],
                           authorized=False)
    assert p and "base_url" in p[0][1]


# --- findings / summary -----------------------------------------------------

def test_fingerprint_is_matrix_row_persona():
    a = am.finding_fingerprint(1, "r1", "2")
    assert a == am.finding_fingerprint(1, "r1", "2")
    assert len({a, am.finding_fingerprint(1, "r1", "3"), am.finding_fingerprint(2, "r1", "2")}) == 3


class _Cell:
    def __init__(self, expect, verdict):
        self.expect, self.verdict = expect, verdict


def test_summary_separates_security_failures_from_regressions():
    s = am.summarize([_Cell("deny", "fail"), _Cell("allow", "fail"), _Cell("deny", "pass"),
                      _Cell("deny", "inconclusive")])
    assert s["access_granted"] == 1 and s["allow_regressions"] == 1
    assert s["total"] == 4 and s["inconclusive"] == 1


# --- dispatch helpers -------------------------------------------------------

def test_strip_identity_drops_session_and_credential_headers():
    settings = {"storage_state": {"cookies": [1]},
                "headers": {"Authorization": "Bearer x", "X-Trace": "1", "Cookie": "a=b"},
                "domain_settings": {"api.example.com": {"headers": {"X-API-Key": "k", "Accept": "json"}}}}
    strip_identity(settings)
    assert "storage_state" not in settings
    assert settings["headers"] == {"X-Trace": "1"}
    assert settings["domain_settings"]["api.example.com"]["headers"] == {"Accept": "json"}


def test_build_cell_job_carries_only_its_own_result_key():
    job = build_cell_job(cell_id=9, row={"kind": "request", "method": "get"}, url="https://a/x",
                         identity={"anonymous": True, "headers": {}}, owner=None,
                         allowed_domains=["a"], authorized=False, data_policy=None, matrix_run_id=4)
    assert job["job_type"] == "access_check"
    assert job["result_key"] == "access_check:result:9"
    assert job["target"]["method"] == "GET"
    assert job["run_id"] == 0
