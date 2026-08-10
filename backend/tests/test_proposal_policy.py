"""Separation of duties on agent proposals — workstream F4.

The proposal queue exists so that an agent's change is reviewed by a human
before it lands. Two things undermined that:

1. **Creating and accepting need the same role, and nothing compared the two
   identities.** An editor could file a proposal and accept it themselves,
   which is a review queue in shape only. Worse, `CaseProposal` had no
   `created_by_id` column at all — there was nothing to compare, so the check
   could not have existed even in principle.

2. **`maybe_auto_apply` applies changes with no human at any point** once the
   workspace threshold is met, recording `decided_by_id = None`. That is a
   deliberate feature, but an operator in a regulated environment needs to be
   able to turn it off for the whole instance and prove it is off.

Defaults are off. A solo user proposing through their own agent and accepting in
the UI is the normal single-person workflow, and enabling enforcement by default
would break every existing install for a control they never asked for. It is
opt-in per workspace, and an instance admin can force it on everywhere.
"""
import pytest

from app.services import instance_settings as insvc
from app.services.proposal_policy import (
    approver_conflict,
    auto_apply_disabled,
    separation_required,
)


@pytest.fixture(autouse=True)
def fresh_cache():
    insvc.invalidate_cache()
    yield
    insvc.invalidate_cache()


def _settings(monkeypatch, **overrides):
    monkeypatch.setattr(insvc, "_load_overrides_sync", lambda: dict(overrides))
    insvc.invalidate_cache()


# --- Is separation required? --------------------------------------------------

def test_separation_is_off_by_default(monkeypatch):
    _settings(monkeypatch)
    assert separation_required(workspace_flag=False) is False


def test_a_workspace_can_opt_in(monkeypatch):
    _settings(monkeypatch)
    assert separation_required(workspace_flag=True) is True


def test_an_instance_admin_can_force_it_everywhere(monkeypatch):
    # The point of the instance switch: an operator must be able to say "this
    # control is on for every workspace" and have that be true regardless of
    # what a workspace admin sets.
    _settings(monkeypatch, REQUIRE_SEPARATE_APPROVER="true")
    assert separation_required(workspace_flag=False) is True


def test_a_workspace_cannot_opt_out_of_the_instance_policy(monkeypatch):
    _settings(monkeypatch, REQUIRE_SEPARATE_APPROVER="true")
    assert separation_required(workspace_flag=False) is True


# --- Who may approve ----------------------------------------------------------

def test_self_approval_is_a_conflict_when_required():
    assert approver_conflict(created_by_id=5, approver_id=5, required=True) is True


def test_self_approval_is_allowed_when_not_required():
    assert approver_conflict(created_by_id=5, approver_id=5, required=False) is False


def test_a_different_person_is_never_a_conflict():
    assert approver_conflict(created_by_id=5, approver_id=6, required=True) is False


def test_an_unattributed_proposal_is_not_a_conflict():
    # Proposals that predate created_by_id have nothing to compare. Treating
    # them as conflicts would freeze every queue that existed before this
    # change; treating them as clean is the honest reading of "we don't know".
    assert approver_conflict(created_by_id=None, approver_id=6, required=True) is False


# --- Auto-apply kill switch ---------------------------------------------------

def test_auto_apply_is_enabled_by_default(monkeypatch):
    _settings(monkeypatch)
    assert auto_apply_disabled() is False


def test_an_instance_admin_can_disable_auto_apply(monkeypatch):
    _settings(monkeypatch, AUTO_APPLY_DISABLED="true")
    assert auto_apply_disabled() is True


def test_the_kill_switch_is_registered_as_a_setting():
    # It has to be an instance setting, not an env var, or "prove auto-apply is
    # off" becomes "read the operator's shell history".
    assert "AUTO_APPLY_DISABLED" in insvc.REGISTRY
    assert "REQUIRE_SEPARATE_APPROVER" in insvc.REGISTRY
