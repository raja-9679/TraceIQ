"""W5 selector-heal candidates against a real Postgres: the aggregator
persists ranked candidates + a redacted snapshot reference, accept rechecks
uniqueness against the snapshot, and the list is access-filtered.

    ./run-tests-live.sh tests/integration/test_heal_candidates_db.py -q
"""
from __future__ import annotations

import os

import pytest
from fastapi import HTTPException
from sqlmodel import select

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api import heal_proposals as hp                     # noqa: E402
from app.core.auth import AuthPrincipal                      # noqa: E402
from app.models import (                                     # noqa: E402
    Project, SelectorHealProposal, Tenant, TestCase, TestRun, TestSuite, User, Workspace,
)
from app.tasks import result_aggregator                      # noqa: E402

from tests.integration.test_targeted_runs_db import (         # noqa: E402,F401
    scene, session,
)

RAW_DOM = """<html><body><form id="login-form">
  <input id="pw" name="password" type="password" value="hunter2">
  <button data-testid="login-submit" class="btn" type="submit">Sign in</button>
</form><div class="footer"><span>Sign in</span></div></body></html>"""


@pytest.fixture
def store(monkeypatch):
    """In-memory object store in place of MinIO."""
    from app.core import storage
    objects = {}

    def upload(fileobj, key, content_type=None):
        objects[key] = fileobj.read()

    def get(key, max_bytes=None):
        if key not in objects:
            raise KeyError(key)
        return objects[key][:max_bytes] if max_bytes else objects[key]
    monkeypatch.setattr(storage.object_store, "upload_fileobj", upload)
    monkeypatch.setattr(storage.object_store, "get_object_bytes", get)
    return objects


def _me(scene):
    return AuthPrincipal(user=scene.user, api_key=None)


async def _proposal_from_worker(session, scene, store):
    case = await session.get(TestCase, scene.root_cases[0])
    case.steps = [{"id": "s1", "type": "click", "selector": "#login-btn", "intent": "submit login"}]
    session.add(case)
    run = TestRun(project_id=scene.project_id, test_suite_id=scene.root_id, test_case_id=case.id)
    session.add(run)
    await session.commit()
    candidates = [
        {"selector": '[data-testid="login-submit"]', "strategy": "test-id", "source": "deterministic",
         "matches": 1, "applicable": True, "score": 0.9,
         "breakdown": {"stability": 1, "similarity": 0.8, "llm": 0, "uniqueness": 1}},
        {"selector": 'text="Sign in"', "strategy": "text", "source": "deterministic",
         "matches": 2, "applicable": False, "score": 0.4,
         "breakdown": {"stability": 0.5, "similarity": 0.8, "llm": 0, "uniqueness": 0}},
    ]
    result_aggregator._persist_heal_suggestions({
        "run_id": run.id,
        "heal_suggestions": [{
            "test_case_id": case.id, "step_id": "s1", "old_selector": "#login-btn",
            "new_selector": '[data-testid="login-submit"]', "matches": 1, "intent": "submit login",
            "candidates": candidates, "dom_snapshot": RAW_DOM, "applied": False,
        }],
    })
    p = (await session.exec(select(SelectorHealProposal).where(
        SelectorHealProposal.test_case_id == case.id))).one()
    return p, run, case


async def test_aggregator_persists_candidates_and_a_redacted_snapshot(session, scene, store):
    p, run, _ = await _proposal_from_worker(session, scene, store)
    assert [c["selector"] for c in p.candidates] == ['[data-testid="login-submit"]', 'text="Sign in"']
    assert p.candidates[1]["applicable"] is False
    assert p.chosen_selector == '[data-testid="login-submit"]'
    assert p.confidence == 0.9
    assert p.dom_snapshot_key.startswith(f"runs/{run.id}/heal/")
    snapshot = store[p.dom_snapshot_key].decode()
    assert "hunter2" not in snapshot                     # backend re-redacted it
    assert 'data-testid="login-submit"' in snapshot

    listed = await hp.list_proposals(status="pending", project_id=scene.project_id,
                                     principal=_me(scene), session=session)
    [read] = [r for r in listed if r.id == p.id]
    assert read.has_dom_snapshot and len(read.candidates) == 2


async def test_accept_refuses_a_candidate_that_is_not_unique(session, scene, store):
    p, _, case = await _proposal_from_worker(session, scene, store)
    with pytest.raises(HTTPException) as exc:
        await hp.accept_proposal(p.id, hp.HealSelectorChoice(selector='text="Sign in"'),
                                 principal=_me(scene), session=session)
    assert exc.value.status_code == 409
    assert "2 elements" in exc.value.detail
    await session.refresh(p)
    await session.refresh(case)
    assert p.status == "pending"
    assert case.steps[0]["selector"] == "#login-btn"


async def test_accept_applies_a_unique_reviewer_pick_and_records_the_recheck(session, scene, store):
    p, _, case = await _proposal_from_worker(session, scene, store)
    dry = await hp.recheck_proposal(p.id, None, principal=_me(scene), session=session)
    assert dry["status"] == "unique"
    out = await hp.accept_proposal(p.id, hp.HealSelectorChoice(selector='[data-testid="login-submit"]'),
                                   principal=_me(scene), session=session)
    assert out["recheck"]["status"] == "unique" and out["recheck"]["matches"] == 1
    await session.refresh(p)
    await session.refresh(case)
    assert p.status == "accepted" and p.recheck["status"] == "unique"
    assert case.steps[0]["selector"] == '[data-testid="login-submit"]'


async def test_accept_refuses_a_selector_outside_the_candidates(session, scene, store):
    p, _, _ = await _proposal_from_worker(session, scene, store)
    with pytest.raises(HTTPException) as exc:
        await hp.accept_proposal(p.id, hp.HealSelectorChoice(selector="body"),
                                 principal=_me(scene), session=session)
    assert exc.value.status_code == 400


async def test_list_hides_proposals_from_projects_the_caller_cannot_read(session, scene, store):
    stranger = User(email="s@else.example.com", full_name="S", hashed_password="x")
    session.add(stranger)
    await session.flush()
    tenant = Tenant(name="Else", owner_id=stranger.id)
    session.add(tenant)
    await session.flush()
    ws = Workspace(name="Else ws", tenant_id=tenant.id)
    session.add(ws)
    await session.flush()
    proj = Project(name="Secret", workspace_id=ws.id)
    session.add(proj)
    await session.flush()
    suite = TestSuite(name="S", project_id=proj.id)
    session.add(suite)
    await session.flush()
    case = TestCase(name="c", test_suite_id=suite.id, project_id=proj.id, steps=[])
    session.add(case)
    await session.flush()
    session.add(SelectorHealProposal(test_case_id=case.id, step_id="x", new_selector="#secret"))
    await session.commit()

    mine = await hp.list_proposals(status="pending", principal=_me(scene), session=session)
    assert all(r.new_selector != "#secret" for r in mine)
    with pytest.raises(HTTPException) as exc:
        p = (await session.exec(select(SelectorHealProposal).where(
            SelectorHealProposal.test_case_id == case.id))).one()
        await hp.recheck_proposal(p.id, None, principal=_me(scene), session=session)
    assert exc.value.status_code == 403
