"""Run completion against a real Postgres (WP15f).

    ./run-tests-live.sh tests/integration/test_run_completion_db.py -q

The claim must hold across sessions: the aggregator and the legacy
/finalize endpoint (or two aggregator processes) finalizing the same run fire
its side effects once.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlmodel import Session, create_engine

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.core.config import db_url_for, settings            # noqa: E402
from app.models import (                                     # noqa: E402
    Project, Tenant, TestRun, TestStatus, TestSuite, User, Workspace,
)
from app.services import run_completion                      # noqa: E402


@pytest.fixture
def engine():
    eng = create_engine(db_url_for(settings.DATABASE_URL, sync=True))
    with Session(eng) as s:
        s.exec(text("TRUNCATE users, tenant, workspace CASCADE"))
        s.commit()
    yield eng
    eng.dispose()


@pytest.fixture
def run_id(engine):
    with Session(engine) as s:
        user = User(email="ci@corp.example.com", full_name="CI", hashed_password="x", is_verified=True)
        s.add(user)
        s.flush()
        tenant = Tenant(name="Corp", owner_id=user.id)
        s.add(tenant)
        s.flush()
        ws = Workspace(name="ws", tenant_id=tenant.id)
        s.add(ws)
        s.flush()
        project = Project(name="Shop", workspace_id=ws.id)
        s.add(project)
        s.flush()
        suite = TestSuite(name="Checkout", project_id=project.id)
        s.add(suite)
        s.flush()
        run = TestRun(test_suite_id=suite.id, project_id=project.id, status=TestStatus.RUNNING)
        s.add(run)
        s.commit()
        return run.id


@pytest.fixture
def queued(monkeypatch):
    calls = []
    monkeypatch.setattr(run_completion, "queue_side_effects",
                        lambda run_id, payload=None, **k: calls.append(run_id) or [])
    return calls


def test_side_effects_fire_after_commit_and_only_once(engine, run_id, queued):
    with Session(engine) as s:
        assert run_completion.finalize_on_commit(s, run_id) is True
        assert queued == []          # not before commit
        s.commit()
    assert queued == [run_id]

    with Session(engine) as s:       # a redelivered result / second aggregator
        assert run_completion.finalize_on_commit(s, run_id) is False
        s.commit()
    assert queued == [run_id]
    with Session(engine) as s:
        assert s.get(TestRun, run_id).finalized_at is not None


def test_rolled_back_claim_fires_nothing_and_can_be_retried(engine, run_id, queued):
    with Session(engine) as s:
        assert run_completion.finalize_on_commit(s, run_id) is True
        s.rollback()
    assert queued == []
    with Session(engine) as s:
        assert s.get(TestRun, run_id).finalized_at is None
        assert run_completion.finalize_on_commit(s, run_id) is True
        s.commit()
    assert queued == [run_id]


def test_concurrent_claimers_one_winner(engine, run_id, queued):
    a, b = Session(engine), Session(engine)
    try:
        assert run_completion.finalize_on_commit(a, run_id) is True
        a.commit()
        # b started before a's commit became visible to it in a real race;
        # READ COMMITTED re-evaluates the WHERE, so b loses either way.
        assert run_completion.finalize_on_commit(b, run_id) is False
        b.commit()
    finally:
        a.close()
        b.close()
    assert queued == [run_id]


def test_aggregator_completion_path_finalizes(engine, run_id, queued, monkeypatch):
    from app.tasks import result_aggregator
    monkeypatch.setattr(result_aggregator, "_update_flake_scores", lambda *a: None)
    progress = {"total": "1", "completed": "1", "passed": "1", "failed": "0"}
    for _ in range(2):               # the second pass is a redelivered result
        with Session(engine) as s:
            run = s.get(TestRun, run_id)
            result_aggregator.update_run_from_progress(run, run_id, progress, s)
            s.add(run)
            s.commit()
    assert queued == [run_id]
    with Session(engine) as s:
        assert s.get(TestRun, run_id).status == TestStatus.PASSED
