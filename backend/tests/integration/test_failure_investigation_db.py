"""Failure investigation v2 history + the analysis task against a real Postgres.

    ./run-tests-live.sh tests/integration/test_failure_investigation_db.py -q
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest
from sqlmodel import Session, create_engine

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.core.config import db_url_for, settings                   # noqa: E402
from app.models import FailureCluster, TestCaseResult, TestRun, TestStatus  # noqa: E402
from app.services import failure_analysis                           # noqa: E402
from app.services.failure_signature import compute_signature        # noqa: E402
from app.tasks import analysis_tasks                                # noqa: E402

from tests.integration.test_targeted_runs_db import scene, session  # noqa: E402,F401

ERR = "Timeout 30000ms exceeded waiting for locator('#qty')"


async def _run(session, scene, project_id, case_id, status, commit, error=None, name="cart", payload=None):
    run = TestRun(test_suite_id=scene.root_id, project_id=project_id, status=status,
                  git_commit=commit, git_repo="https://github.com/acme/shop")
    session.add(run)
    await session.flush()
    res = TestCaseResult(test_run_id=run.id, test_name=name, status=status, duration_ms=10,
                         error_message=error, test_case_id=case_id, result_payload=payload)
    session.add(res)
    await session.commit()
    return run, res


def _sync():
    return Session(create_engine(db_url_for(settings.DATABASE_URL, sync=True)))


async def test_history_is_the_cases_own_prior_runs_only(session, scene):
    case = scene.root_cases[0]
    await _run(session, scene, scene.project_id, case, TestStatus.PASSED, "c1")
    _, f2 = await _run(session, scene, scene.project_id, case, TestStatus.FAILED, "c2", ERR)
    # Noise that must NOT show up: another case, another data row name,
    # another project's run that claims this case id.
    await _run(session, scene, scene.project_id, scene.root_cases[1], TestStatus.FAILED, "x", ERR)
    await _run(session, scene, scene.project_id, case, TestStatus.FAILED, "x", ERR, name="cart [row 2]")
    await _run(session, scene, scene.other_project_id, case, TestStatus.FAILED, "x", ERR)
    run, res = await _run(session, scene, scene.project_id, case, TestStatus.FAILED, "c3", ERR)
    # A later run is not "history" for this one.
    await _run(session, scene, scene.project_id, case, TestStatus.PASSED, "c4")

    session.add(FailureCluster(project_id=scene.project_id, signature=compute_signature(ERR, "cart")[0],
                               title="t", category="timeout", occurrence_count=4))
    await session.commit()

    with _sync() as s:
        history, change = analysis_tasks.case_history(s, s.get(TestRun, run.id), s.get(TestCaseResult, res.id), case)

    assert [e.status for e in history.entries] == ["failed", "passed"]
    assert history.entries[0].result_id == f2.id and history.entries[0].same_signature
    assert history.failing_streak == 2
    assert history.first_failing_commit == "c2"
    assert history.last_pass_commit == "c1"
    assert history.cluster.occurrence_count == 4
    assert change.from_commit == "c1" and change.to_commit == "c3"
    assert change.compare_url == "https://github.com/acme/shop/compare/c1...c3"


async def test_analysis_task_writes_a_v2_report(session, scene, monkeypatch):
    monkeypatch.setattr(failure_analysis, "get_provider", lambda pid=None: SimpleNamespace(name="null"))
    case = scene.root_cases[0]
    await _run(session, scene, scene.project_id, case, TestStatus.PASSED, "c1")
    evidence = [
        {"id": "s0", "index": 0, "type": "click", "status": "passed",
         "requests": [{"id": "s0.r0", "method": "POST", "url": "https://shop.test/api/cart", "status": 502}]},
        {"id": "s1", "index": 1, "type": "fill", "selector": "#qty", "status": "failed", "error": ERR},
    ]
    run, res = await _run(session, scene, scene.project_id, case, TestStatus.FAILED, "c2", ERR,
                          payload={"step_evidence": evidence})

    out = analysis_tasks.analyze_run_failures(run.id)
    assert out["status"] == "ok"

    with _sync() as s:
        stored = json.loads(s.get(TestCaseResult, res.id).ai_analysis)
        rollup = s.get(TestRun, run.id).ai_analysis
    assert stored["schema_version"] == 2
    assert stored["result_id"] == res.id
    assert stored["hypotheses"][0]["category"] == "app_bug"
    assert "s0.r0" in stored["hypotheses"][0]["evidence_refs"]
    assert stored["history"]["last_pass_commit"] == "c1"
    assert stored["changed_since_last_pass"]["from_commit"] == "c1"
    assert rollup["reports"][0]["observed"]
