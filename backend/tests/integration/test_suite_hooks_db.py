"""Suite setup/teardown hooks + data seed against a real Postgres (WP5).

    ./run-tests-live.sh tests/integration/test_suite_hooks_db.py -q

Covers what only a real database proves: the seed is stored on the run (from
the POST body, or recorded from the suite chain at dispatch), hook settings
are validated on save, and the hook chain + run context actually reach the
dispatched job payload.
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api.endpoints import test_runs, test_suites               # noqa: E402
from app.core.auth import AuthPrincipal                             # noqa: E402
from app.models import TestRun, TestSuite, TestSuiteUpdate          # noqa: E402
from app.services.suite_hooks import run_short_token                # noqa: E402

from tests.integration.test_targeted_runs_db import (               # noqa: E402,F401
    no_queue, scene, session,
)


class FakePipe:
    def __init__(self, sink):
        self.sink = sink

    def xadd(self, stream, fields):
        self.sink.append(json.loads(fields["payload"]))

    def rpush(self, key, payload):
        self.sink.append(json.loads(payload))

    def __getattr__(self, _name):          # sadd / hset / expire / execute
        return lambda *a, **kw: None


class FakeRedis:
    def __init__(self):
        self.jobs = []

    def pipeline(self):
        return FakePipe(self.jobs)

    def __getattr__(self, _name):          # xgroup_create, hset, …
        return lambda *a, **kw: None


@pytest.fixture
def redis_jobs(monkeypatch):
    from app import worker
    fake = FakeRedis()
    monkeypatch.setattr(worker, "redis_client", fake)
    return fake.jobs


ROOT_HOOKS = {
    "setup_steps": [{"type": "http-request", "value": "https://api.test/login",
                     "params": {"method": "POST", "extract": [{"path": "token", "variable": "token"}]}}],
    "teardown_steps": [{"type": "http-request", "value": "https://api.test/logout"}],
    "data_seed": "suite-seed",
}
CHILD_HOOKS = {
    "setup_steps": [{"type": "http-request", "value": "https://api.test/cart",
                     "params": {"method": "POST", "cleanup": {"url": "/cart/{{cart_id}}"}}}],
}


async def _set_settings(session, scene, suite_id, settings):
    return await test_suites.update_test_suite(
        suite_id, TestSuiteUpdate(settings=settings), session=session, current_user=scene.user)


async def _run_child(session, scene, **ctx):
    runs = await test_runs.create_run(
        suite_id=scene.child_id, case_id=None, browser=["chromium"], device=None, tags=None,
        context=test_runs.RunCreateContext(**ctx),
        session=session, principal=AuthPrincipal(user=scene.user, api_key=None))
    return runs[0] if isinstance(runs, list) else runs


async def test_hook_settings_are_validated_and_normalised_on_save(session, scene):
    saved = await _set_settings(session, scene, scene.root_id, {**ROOT_HOOKS, "headers": {"X-Suite": "root"}})
    assert saved.settings["setup_steps"][0]["id"].startswith("step-")
    assert saved.settings["headers"] == {"X-Suite": "root"}
    with pytest.raises(HTTPException) as exc:
        await _set_settings(session, scene, scene.root_id, {"setup_steps": [{"value": "no type"}]})
    assert exc.value.status_code == 400


async def test_run_body_seed_is_stored_on_the_run(session, scene):
    run = await _run_child(session, scene, data_seed=20261015)
    stored = await session.get(TestRun, run.id)
    assert stored.data_seed == "20261015"
    with pytest.raises(HTTPException) as exc:
        await _run_child(session, scene, data_seed="x" * 65)
    assert exc.value.status_code == 400


async def test_dispatch_ships_hook_chain_and_run_context_and_records_suite_seed(session, scene, redis_jobs):
    from app import worker
    await _set_settings(session, scene, scene.root_id, ROOT_HOOKS)
    await _set_settings(session, scene, scene.child_id, CHILD_HOOKS)
    run = await _run_child(session, scene)
    assert run.data_seed is None

    worker.run_test_suite(run.id)

    assert len(redis_jobs) == 2          # one job per child case (CONTINUOUS dispatch)
    job = redis_jobs[0]
    chain = job["test_case"]["suite_hooks"]
    assert [f["suite_id"] for f in chain] == [scene.root_id, scene.child_id]
    assert chain[0]["setup_steps"][0]["value"] == "https://api.test/login"
    assert chain[0]["teardown_steps"][0]["value"] == "https://api.test/logout"
    assert chain[1]["setup_steps"][0]["params"]["cleanup"] == {"url": "/cart/{{cart_id}}"}

    ctx = job["settings"]["run"]
    assert ctx["id"] == run.id
    assert ctx["short"] == run_short_token(run.id)
    assert ctx["data_seed"] == "suite-seed"      # inherited from the root suite
    assert ctx["started_at"].endswith("Z")

    stored = await session.get(TestRun, run.id)
    await session.refresh(stored)
    assert stored.data_seed == "suite-seed"      # recorded so it can be replayed


async def test_run_seed_wins_and_isolated_suite_drops_parent_hooks(session, scene, redis_jobs):
    from app import worker
    await _set_settings(session, scene, scene.root_id, ROOT_HOOKS)
    await _set_settings(session, scene, scene.child_id, CHILD_HOOKS)
    child = await session.get(TestSuite, scene.child_id)
    child.inherit_settings = False
    session.add(child)
    await session.commit()

    run = await _run_child(session, scene, data_seed="replay-7")
    worker.run_test_suite(run.id)

    job = redis_jobs[0]
    assert [f["suite_id"] for f in job["test_case"]["suite_hooks"]] == [scene.child_id]
    assert job["settings"]["run"]["data_seed"] == "replay-7"
    stored = await session.get(TestRun, run.id)
    await session.refresh(stored)
    assert stored.data_seed == "replay-7"


async def test_accepted_settings_proposal_keeps_hooks(session, scene):
    from app.api import agent_ownership
    from app.models import CaseProposal, CaseProposalAction
    await _set_settings(session, scene, scene.root_id, ROOT_HOOKS)
    proposal = CaseProposal(
        project_id=scene.project_id, test_suite_id=scene.root_id,
        action=CaseProposalAction.UPDATE_SUITE_SETTINGS,
        payload={"settings": {"headers": {"X-New": "1"},
                              "teardown_steps": [{"type": "http-request", "value": "https://api.test/wipe"}]}},
    )
    session.add(proposal)
    await session.commit()
    await agent_ownership._apply_proposal(proposal, scene.user.id, session)
    await session.commit()
    suite = await session.get(TestSuite, scene.root_id)
    await session.refresh(suite)
    assert suite.settings["headers"]["X-New"] == "1"
    assert suite.settings["setup_steps"][0]["value"] == "https://api.test/login"    # kept
    assert suite.settings["teardown_steps"][0]["value"] == "https://api.test/wipe"  # replaced
    assert suite.settings["data_seed"] == "suite-seed"
