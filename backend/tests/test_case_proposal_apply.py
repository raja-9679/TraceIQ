"""_apply_proposal semantics for the agent proposal queue.

Invariants:
  1. UPDATE applies every documented patch key — name, steps, code_paths,
     tags, priority — not just the first three (tags/priority used to be
     silently dropped while the MCP docstring advertised them).
  2. CREATE carries tags/priority onto the new case.
  3. UPDATE_SUITE_SETTINGS merges the proposed settings over the suite's
     existing blob by default (proposed wins per key), replaces wholesale
     with merge=false, and toggles inherit_settings.
  4. maybe_auto_apply never applies UPDATE_SUITE_SETTINGS regardless of
     confidence — suite headers reach the app under test with real
     credentials, so a human reviews every change.

Run with:
    cd backend && pytest tests/test_case_proposal_apply.py -v
"""
from unittest.mock import MagicMock

import pytest

from app.models import (
    CaseProposal,
    CaseProposalAction,
    TestCase,
    TestSuite,
)


class FakeSession:
    def __init__(self, objects):
        # objects: {model_class: instance} returned by session.get
        self.objects = objects
        self.added = []

    async def get(self, model, pk):
        return self.objects.get(model)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass


@pytest.fixture(autouse=True)
def _noop_revisions(monkeypatch):
    async def record_revision(*a, **kw):
        return None
    import app.services.case_revisions as revisions
    monkeypatch.setattr(revisions, "record_revision", record_revision)


async def test_update_applies_tags_and_priority():
    from app.api.agent_ownership import _apply_proposal

    case = TestCase(id=1, name="old", steps=[], test_suite_id=1, project_id=1,
                    tags=["old-tag"], priority="low")
    proposal = CaseProposal(
        id=10, project_id=1, target_case_id=1,
        action=CaseProposalAction.UPDATE,
        payload={"tags": ["smoke", "articles"], "priority": "critical"},
    )
    await _apply_proposal(proposal, user_id=1,
                          session=FakeSession({TestCase: case}))
    assert case.tags == ["smoke", "articles"]
    assert case.priority == "critical"
    assert case.name == "old"  # untouched keys stay


async def test_apply_assigns_missing_step_ids():
    """Agent steps arrive without ids; stored verbatim they 500 every read
    of the case (TestStep.id used to be required). Apply must fill them."""
    from app.api.agent_ownership import _apply_proposal

    case = TestCase(id=1, name="c", steps=[], test_suite_id=1, project_id=1)
    proposal = CaseProposal(
        id=20, project_id=1, target_case_id=1,
        action=CaseProposalAction.UPDATE,
        payload={"steps": [{"type": "goto", "value": "http://x"},
                           {"id": "keep-me", "type": "click", "selector": "#a"}]},
    )
    await _apply_proposal(proposal, user_id=1,
                          session=FakeSession({TestCase: case}))
    assert all(s.get("id") for s in case.steps)
    assert case.steps[1]["id"] == "keep-me"  # existing ids are preserved


async def test_create_infers_mobile_executor_from_steps():
    """A proposed case whose steps are all mobile-* must land on the mobile
    worker even when the payload omits executor — defaulting to
    ui_playwright routed it to web workers, which skipped every step and
    reported a fake pass."""
    from app.api.agent_ownership import _apply_proposal

    session = FakeSession({})
    proposal = CaseProposal(
        id=21, project_id=1, test_suite_id=2,
        action=CaseProposalAction.CREATE,
        payload={"name": "m", "steps": [
            {"type": "mobile-launch-app"},
            {"type": "mobile-tap", "selector": "~go"}]},
    )
    await _apply_proposal(proposal, user_id=1, session=session)
    created = [o for o in session.added if isinstance(o, TestCase)]
    assert created and created[0].executor == "mobile_appium"


async def test_create_rejects_unknown_executor():
    from app.api.agent_ownership import _apply_proposal
    from fastapi import HTTPException

    proposal = CaseProposal(
        id=22, project_id=1, test_suite_id=2,
        action=CaseProposalAction.CREATE,
        payload={"name": "x", "steps": [], "executor": "quantum"},
    )
    with pytest.raises(HTTPException) as exc:
        await _apply_proposal(proposal, user_id=1, session=FakeSession({}))
    assert exc.value.status_code == 400


async def test_create_carries_tags_and_priority():
    from app.api.agent_ownership import _apply_proposal

    session = FakeSession({})
    proposal = CaseProposal(
        id=11, project_id=1, test_suite_id=2,
        action=CaseProposalAction.CREATE,
        payload={"name": "n", "steps": [], "tags": ["smoke"],
                 "priority": "high"},
    )
    await _apply_proposal(proposal, user_id=1, session=session)
    created = [o for o in session.added if isinstance(o, TestCase)]
    assert created and created[0].tags == ["smoke"]
    assert created[0].priority == "high"


async def test_suite_settings_merge_default():
    from app.api.agent_ownership import _apply_proposal

    suite = TestSuite(
        id=5, name="s", project_id=1,
        settings={"headers": {"X-Keep": "1", "X-Old": "a"},
                  "params": {"p": "1"}},
        inherit_settings=True,
    )
    proposal = CaseProposal(
        id=12, project_id=1, test_suite_id=5,
        action=CaseProposalAction.UPDATE_SUITE_SETTINGS,
        payload={"settings": {"headers": {"X-Old": "b", "X-New": "c"}},
                 "merge": True},
    )
    await _apply_proposal(proposal, user_id=1,
                          session=FakeSession({TestSuite: suite}))
    assert suite.settings["headers"] == {
        "X-Keep": "1", "X-Old": "b", "X-New": "c"}
    assert suite.settings["params"] == {"p": "1"}  # untouched key survives


async def test_suite_settings_replace_and_inherit_toggle():
    from app.api.agent_ownership import _apply_proposal

    suite = TestSuite(
        id=6, name="s", project_id=1,
        settings={"headers": {"X-Old": "a"}, "params": {"p": "1"}},
        inherit_settings=True,
    )
    proposal = CaseProposal(
        id=13, project_id=1, test_suite_id=6,
        action=CaseProposalAction.UPDATE_SUITE_SETTINGS,
        payload={"settings": {"headers": {"X-New": "c"}},
                 "inherit_settings": False, "merge": False},
    )
    await _apply_proposal(proposal, user_id=1,
                          session=FakeSession({TestSuite: suite}))
    assert suite.settings == {"headers": {"X-New": "c"}}
    assert suite.inherit_settings is False


async def test_suite_settings_never_auto_applied():
    from app.api.agent_ownership import maybe_auto_apply

    proposal = CaseProposal(
        id=14, project_id=1, test_suite_id=5,
        action=CaseProposalAction.UPDATE_SUITE_SETTINGS,
        payload={"settings": {"headers": {"Authorization": "Bearer x"}}},
        ai_confidence=1.0, status="pending",
    )
    applied = await maybe_auto_apply(proposal, user_id=1, session=MagicMock())
    assert applied is False
