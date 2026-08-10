"""Who may approve an agent proposal, and whether policy may approve at all.

Workstream F4. The proposal queue (`app/api/agent_ownership.py`) exists so a
human reviews an agent's change before it lands. Two holes:

* Creating and accepting required the *same* role, and nothing compared the two
  identities — so an editor could file a proposal and accept it themselves.
  `CaseProposal` did not even carry a creator, so the check could not have
  existed. Migration `e0f1a2b3c4d5` adds `created_by_id`.
* `maybe_auto_apply` applies CREATE/UPDATE with no human at any point once the
  workspace confidence threshold is met. That is a wanted feature, but an
  operator in a regulated environment has to be able to switch it off for the
  whole instance and *show* that it is off — which means an instance setting,
  not an environment variable somebody has to be trusted about.

Both controls default to off. A solo user whose own agent files proposals and
who then accepts them in the UI is the normal single-person workflow; turning
enforcement on by default would break every existing install for a control
nobody asked for. Workspaces opt in, and an instance admin can force it on
everywhere — a workspace cannot opt back out of that.
"""
from __future__ import annotations

from typing import Optional

from app.services.instance_settings import effective


def separation_required(*, workspace_flag: bool) -> bool:
    """True when the approver must be someone other than the proposer.

    The instance policy is a floor, not a default: if an instance admin turns it
    on, a workspace admin cannot turn it off.
    """
    if bool(effective("REQUIRE_SEPARATE_APPROVER")):
        return True
    return bool(workspace_flag)


def approver_conflict(*, created_by_id: Optional[int], approver_id: Optional[int],
                      required: bool) -> bool:
    """True when this person must not be the one to approve this proposal.

    An unattributed proposal (no `created_by_id` — every row that predates the
    column) is not a conflict. Treating "we don't know" as a violation would
    freeze every queue that existed before this change.
    """
    if not required:
        return False
    if created_by_id is None or approver_id is None:
        return False
    return created_by_id == approver_id


def auto_apply_disabled() -> bool:
    """Instance-wide kill switch for policy-driven (human-free) application."""
    return bool(effective("AUTO_APPLY_DISABLED"))
