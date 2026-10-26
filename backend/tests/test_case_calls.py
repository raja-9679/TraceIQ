"""'Run another test' steps (app/services/case_calls.py)."""
from types import SimpleNamespace

import pytest

from app.services.case_calls import (
    MAX_CALL_DEPTH, CaseCallError, _graph_problem, callers_of, expand_steps,
    validate_overrides,
)


def _case(cid, steps, project_id=1, executor="ui_playwright", name=None):
    return SimpleNamespace(id=cid, steps=steps, project_id=project_id,
                           executor=executor, name=name or f"case {cid}")


def _call(cid, sid="c1", overrides=None):
    params = {"case_id": cid}
    if overrides is not None:
        params["overrides"] = overrides
    return {"id": sid, "type": "run-test", "params": params}


LOGIN = _case(42, [
    {"id": "l1", "type": "goto", "value": "{{env.BASE}}/login"},
    {"id": "l2", "type": "fill", "selector": "#u", "value": "{{env.LOGIN_USER}}"},
    {"id": "l3", "type": "fill", "selector": "#p", "value": "{{ secret.LOGIN_PASSWORD }}"},
], name="Login")


def _expand(steps, cases, case_id=1, project_id=1, executor="ui_playwright"):
    return expand_steps(steps, case_id=case_id, project_id=project_id,
                        executor=executor, load=lambda cid: cases.get(cid))


def test_steps_without_calls_come_back_untouched():
    steps = [{"id": "a", "type": "goto", "value": "x"}]
    assert _expand(steps, {}) is steps


def test_call_is_replaced_in_place_with_origin():
    steps = [{"id": "a", "type": "goto", "value": "/article"}, _call(42),
             {"id": "b", "type": "expect-visible", "selector": ".premium"}]
    out = _expand(steps, {42: LOGIN})
    assert [s["id"] for s in out] == ["a", "l1", "l2", "l3", "b"]
    assert out[1]["from"] == {"case_id": 42, "case_name": "Login", "call_id": "c1", "depth": 1}
    assert "from" not in out[0] and "from" not in out[4]
    # Default: the same variables as the caller — tokens left for the worker.
    assert out[2]["value"] == "{{env.LOGIN_USER}}"
    # The called case is not modified.
    assert "from" not in LOGIN.steps[0]


def test_overrides_apply_only_inside_that_call():
    steps = [_call(42, "c1", {"env.LOGIN_USER": "sub@x.in",
                              "{{secret.LOGIN_PASSWORD}}": "{{secret.SUB_PASSWORD}}"}),
             _call(42, "c2")]
    out = _expand(steps, {42: LOGIN})
    assert out[1]["value"] == "sub@x.in"
    assert out[2]["value"] == "{{secret.SUB_PASSWORD}}"
    assert out[0]["value"] == "{{env.BASE}}/login"
    assert out[4]["value"] == "{{env.LOGIN_USER}}"
    assert out[4]["from"]["call_id"] == "c2"


def test_override_values_are_not_rescanned():
    case = _case(5, [{"id": "x", "type": "fill", "value": "{{a}} {{b}}"}])
    out = _expand([_call(5, overrides={"a": "{{b}}", "b": "B"})], {5: case})
    assert out[0]["value"] == "{{b}} B"


def test_nested_calls_keep_the_innermost_origin():
    cart = _case(7, [_call(42, "inner"), {"id": "k1", "type": "click", "selector": "#buy"}],
                 name="Buy")
    out = _expand([_call(7, "outer")], {7: cart, 42: LOGIN})
    assert [s["id"] for s in out] == ["l1", "l2", "l3", "k1"]
    assert out[0]["from"]["case_id"] == 42 and out[0]["from"]["depth"] == 2
    assert out[3]["from"] == {"case_id": 7, "case_name": "Buy", "call_id": "outer", "depth": 1}


@pytest.mark.parametrize("cases, needle", [
    ({}, "no longer exists"),
    ({42: _case(42, [{"id": "x", "type": "goto"}], project_id=2)}, "no longer exists"),
    ({42: _case(42, [{"id": "x", "type": "mobile-tap"}], executor="mobile_appium")}, "different kind"),
    ({42: _case(42, [])}, "has no steps"),
])
def test_broken_calls_become_failing_steps(cases, needle):
    out = _expand([_call(42)], cases)
    assert len(out) == 1 and out[0]["type"] == "run-test"
    assert needle in out[0]["params"]["error"]


def test_loops_fail_instead_of_recursing():
    a = _case(1, [_call(2, "to-b")])
    b = _case(2, [{"id": "b1", "type": "click"}, _call(1, "to-a")])
    out = _expand(a.steps, {1: a, 2: b}, case_id=1)
    assert out[0]["id"] == "b1"
    assert "run itself" in out[1]["params"]["error"]


def test_depth_limit():
    cases = {i: _case(i, [_call(i + 1, f"c{i}")]) for i in range(2, 20)}
    out = _expand([_call(2)], cases)
    assert "levels deep" in out[-1]["params"]["error"]


def test_save_check_rules():
    a = _case(1, [_call(2)])
    b = _case(2, [_call(1)])
    assert "running itself" in _graph_problem(1, 1, "ui_playwright", a.steps, {1: a, 2: b})
    assert _graph_problem(1, 1, "ui_playwright", [_call(42)], {42: LOGIN}) is None
    assert "does not exist" in _graph_problem(1, 1, "ui_playwright", [_call(9)], {})
    assert "Pick the test" in _graph_problem(1, 1, "ui_playwright",
                                             [{"id": "x", "type": "run-test", "params": {}}], {})
    assert "Only web and mobile" in _graph_problem(1, 1, "manual", [_call(42)], {42: LOGIN})
    chain = {i: _case(i, [_call(i + 1)]) for i in range(2, 2 + MAX_CALL_DEPTH)}
    chain[2 + MAX_CALL_DEPTH] = _case(2 + MAX_CALL_DEPTH, [{"id": "z", "type": "click"}])
    assert "levels deep" in _graph_problem(1, 1, "ui_playwright", [_call(2)], chain)


def test_override_validation():
    assert validate_overrides({"env.A": "1", "{{ data.row-x }}": 2}) == {"env.A": "1", "data.row-x": "2"}
    for bad in ({"fake.email": "x"}, {"env.A": ["x"]}, "nope", {"env.A": "x" * 3000}):
        with pytest.raises(CaseCallError):
            validate_overrides(bad)


def test_callers_of():
    cases = [_case(1, [_call(42)]), _case(2, [{"id": "x", "type": "goto"}]), _case(3, [_call(7)])]
    assert [c.id for c in callers_of(cases, 42)] == [1]


def test_evidence_keeps_a_well_formed_origin_only():
    from app.tasks.result_aggregator import normalise_step_evidence
    out = normalise_step_evidence([
        {"index": 0, "type": "fill", "from": {"case_id": 42, "case_name": "Login",
                                               "call_id": "c1", "depth": 2, "extra": "x"}},
        {"index": 1, "type": "click", "from": {"case_id": "42"}},
        {"index": 2, "type": "click", "from": {"case_id": 9, "depth": 99}},
    ])
    assert out[0]["from"] == {"case_id": 42, "case_name": "Login", "call_id": "c1", "depth": 2}
    assert "from" not in out[1]
    assert out[2]["from"] == {"case_id": 9, "case_name": "#9", "call_id": "", "depth": 1}


def test_suite_hooks_refuse_run_test():
    from app.services.suite_hooks import SuiteHookError, validate_suite_settings
    with pytest.raises(SuiteHookError):
        validate_suite_settings({"setup_steps": [_call(42)]})
