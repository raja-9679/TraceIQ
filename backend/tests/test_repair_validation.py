"""Validated repairs (WP16c, decision V4) — pure logic and policy parity.

Live-DB coverage (endpoints, dispatch overlay, aggregate exclusion) is in
tests/integration/test_env_lock_validation_db.py.
"""
import itertools
from types import SimpleNamespace

import pytest

from app.models import CaseProposalAction, TestStatus
from app.services import policy, proposal_policy
from app.services import repair_validation as rv
from app.services.policy import PolicyContext, evaluate_sync


@pytest.fixture(autouse=True)
def no_deny_log(monkeypatch):
    monkeypatch.setattr(policy, "_write_sync", lambda *a, **k: None)
    policy.reset_dedupe()


def _instance(monkeypatch, require=False, auto_off=False):
    monkeypatch.setattr(proposal_policy, "effective", lambda key: {
        "REQUIRE_REPAIR_VALIDATION": require, "AUTO_APPLY_DISABLED": auto_off}.get(key))


# --- what can be validated ---------------------------------------------------

def test_validatable_proposals():
    assert rv.is_validatable(rv.HEAL, SimpleNamespace())
    upd = SimpleNamespace(action=CaseProposalAction.UPDATE, target_case_id=3, payload={"steps": []})
    assert rv.is_validatable(rv.CASE, upd)
    assert not rv.is_validatable(rv.CASE, SimpleNamespace(
        action=CaseProposalAction.UPDATE, target_case_id=3, payload={"tags": ["x"]}))
    for action in (CaseProposalAction.CREATE, CaseProposalAction.DELETE, CaseProposalAction.MOVE,
                   CaseProposalAction.UPDATE_SUITE_SETTINGS):
        assert not rv.is_validatable(rv.CASE, SimpleNamespace(
            action=action, target_case_id=3, payload={"steps": []}))


# --- policy parity: proposal.approve / proposal.auto_apply ------------------

STATUSES = [None, rv.VALIDATING, rv.VALIDATED, rv.FAILED]


def _legacy_block(instance, ws, protected, validatable, status, vsel, sel):
    required = proposal_policy.validation_required(workspace_flag=ws, suite_protected=protected)
    return proposal_policy.validation_block(validatable=validatable, required=required,
                                            status=status, validated_selector=vsel, selector=sel)


@pytest.mark.parametrize("instance,ws,protected,validatable,status,sels", list(itertools.product(
    [False, True], [False, True], [False, True], [False, True], STATUSES,
    [(None, None), ("#a", "#a"), ("#a", "#b")])))
def test_validation_rule_parity(monkeypatch, instance, ws, protected, validatable, status, sels):
    _instance(monkeypatch, require=instance)
    guard = rv.RepairGuard(kind=rv.HEAL, proposal_id=1, validatable=validatable, workspace_flag=ws,
                           protected_suite_id=7 if protected else None, status=status,
                           validated_selector=sels[0], selector=sels[1])
    d = evaluate_sync("proposal.approve", PolicyContext(facts={"repair": guard}),
                      only=["proposal.validation_required"])
    legacy = _legacy_block(instance, ws, protected, validatable, status, *sels)
    assert (not d.allowed) is (legacy is not None)
    required = instance or ws or protected
    if required and validatable and status != rv.VALIDATED:
        assert not d.allowed and d.status_code == 409
        assert d.detail["error"] == "validation_required"
    if not required or not validatable:
        assert d.allowed


def test_a_validated_heal_counts_only_for_the_selector_it_ran():
    msg = proposal_policy.validation_block(validatable=True, required=True, status=rv.VALIDATED,
                                           validated_selector="#a", selector="#b")
    assert msg and "'#a'" in msg


@pytest.mark.parametrize("protected,auto_off", list(itertools.product([False, True], [False, True])))
def test_auto_apply_parity(monkeypatch, protected, auto_off):
    _instance(monkeypatch, auto_off=auto_off)
    guard = rv.RepairGuard(kind=rv.CASE, proposal_id=1, validatable=False, workspace_flag=False,
                           protected_suite_id=4 if protected else None, status=None)
    d = evaluate_sync("proposal.auto_apply", PolicyContext(
        facts={"proposal": SimpleNamespace(id=1), "repair": guard}))
    legacy_protected = proposal_policy.protected_block(protected_suite_id=guard.protected_suite_id)
    assert d.allowed is (legacy_protected is None and not proposal_policy.auto_apply_disabled())
    if protected:
        assert d.rule == "proposal.protected_suite"


def test_auto_apply_waits_when_validation_is_required(monkeypatch):
    _instance(monkeypatch)
    guard = rv.RepairGuard(kind=rv.CASE, proposal_id=1, validatable=True, workspace_flag=True,
                           protected_suite_id=None, status=None)
    d = evaluate_sync("proposal.auto_apply", PolicyContext(facts={"repair": guard}))
    assert not d.allowed and d.rule == "proposal.validation_required"


def test_rules_have_no_opinion_without_repair_facts(monkeypatch):
    _instance(monkeypatch, require=True)
    assert evaluate_sync("proposal.auto_apply", PolicyContext(facts={"proposal": SimpleNamespace(id=1)})).allowed
    assert evaluate_sync("proposal.approve", PolicyContext(), only=["proposal.validation_required"]).allowed


def test_catalog_lists_the_new_rules():
    names = {(r["action"], r["name"]) for r in policy.catalog()}
    for expected in [("run.dispatch", "environment.exclusive_lock"),
                     ("proposal.approve", "proposal.validation_required"),
                     ("proposal.auto_apply", "proposal.validation_required"),
                     ("proposal.auto_apply", "proposal.protected_suite")]:
        assert expected in names


# --- the overlay --------------------------------------------------------------

class _Case:
    def __init__(self):
        self.id, self.name, self.project_id = 3, "Checkout", 1
        self.steps = [{"id": "s1", "type": "click", "selector": "#old"}, {"id": "s2", "type": "goto"}]


def test_overlay_case_never_writes_through():
    case = _Case()
    o = rv.OverlayCase(case, steps=[{"id": "s1", "selector": "#new"}])
    o.name = "changed"
    assert o.steps[0]["selector"] == "#new" and o.name == "changed" and o.id == 3
    assert case.steps[0]["selector"] == "#old" and case.name == "Checkout"


class _Sess:
    def __init__(self, obj):
        self.obj = obj

    def get(self, model, ident):
        return self.obj


def _heal(**kw):
    base = dict(id=8, status="pending", test_case_id=3, step_id="s1", chosen_selector="#chosen",
                new_selector="#new", validation_conditions={"selector": "#picked"})
    base.update(kw)
    return SimpleNamespace(**base)


def test_heal_overlay_replaces_one_selector_on_a_copy():
    case = _Case()
    run = SimpleNamespace(validation_proposal_kind=rv.HEAL, validation_proposal_id=8)
    out = rv.overlay_for_run(run, case, _Sess(_heal()))
    assert out["steps"][0]["selector"] == "#picked" and out["steps"][1] == case.steps[1]
    assert case.steps[0]["selector"] == "#old"


@pytest.mark.parametrize("proposal,why", [
    (None, "no longer exists"),
    (_heal(status="accepted"), "already accepted"),
    (_heal(test_case_id=99), "another case"),
    (_heal(step_id="gone"), "no longer exists in the case"),
])
def test_overlay_refuses_instead_of_running_the_stored_test(proposal, why):
    run = SimpleNamespace(validation_proposal_kind=rv.HEAL, validation_proposal_id=8)
    with pytest.raises(ValueError, match=why):
        rv.overlay_for_run(run, _Case(), _Sess(proposal))


def test_case_overlay_uses_the_proposed_steps():
    prop = SimpleNamespace(id=2, status="pending", target_case_id=3,
                           payload={"steps": [{"type": "goto", "value": "/new"}]})
    run = SimpleNamespace(validation_proposal_kind=rv.CASE, validation_proposal_id=2)
    out = rv.overlay_for_run(run, _Case(), _Sess(prop))
    assert out["steps"][0]["value"] == "/new" and out["steps"][0].get("id")


def test_harden_settings_switches_retries_off():
    s = rv.harden_settings({"auto_retry": True, "max_retries": 3})
    assert s == {"auto_retry": False, "max_retries": 0, "validation_run": True}
    from app.worker import _settings_payload
    payload = _settings_payload(s)
    assert payload["validation_run"] is True and "auto_retry" not in payload


# --- outcome --------------------------------------------------------------------

def _r(status):
    return SimpleNamespace(status=status)


def test_verdict():
    run = SimpleNamespace(id=1, status=TestStatus.PASSED)
    assert rv.verdict(run, [_r(TestStatus.PASSED)], healed=False) is None
    assert "re-pointed" in rv.verdict(run, [_r(TestStatus.PASSED)], healed=True)
    assert "no results" in rv.verdict(run, [], healed=False)
    assert "did not pass" in rv.verdict(run, [_r(TestStatus.PASSED), _r(TestStatus.FAILED)], False)
    for st in (TestStatus.FAILED, TestStatus.ERROR, TestStatus.CANCELLED, TestStatus.TIMED_OUT):
        assert "ended" in rv.verdict(SimpleNamespace(id=1, status=st), [_r(TestStatus.PASSED)], False)


def test_conditions():
    src = SimpleNamespace(id=4, browser="firefox", device="iPhone 13", environment_id=2,
                          persona_id=None, anonymous=None, emulation=None)
    cond = rv.conditions_from(src)
    assert cond["matches_source"] and cond["source_run_id"] == 4
    same = SimpleNamespace(browser="firefox", device="iPhone 13", environment_id=2)
    assert rv.conditions_matched(cond, same)
    assert not rv.conditions_matched(cond, SimpleNamespace(browser="chromium", device="iPhone 13",
                                                           environment_id=2))
    default = rv.conditions_from(None)
    assert default["browser"] == "chromium" and not rv.conditions_matched(default, same)


def test_run_scope_predicates_compile():
    from sqlalchemy.dialects import postgresql
    from app.services.run_scope import counted, counted_result, is_counted
    assert "is_validation" in str(counted().compile(dialect=postgresql.dialect()))
    assert "NOT IN" in str(counted_result().compile(dialect=postgresql.dialect())).upper()
    assert is_counted(SimpleNamespace()) and not is_counted(SimpleNamespace(is_validation=True))
