"""Demo extras — proposals, failure investigation, value-report history
(app/services/demo_seed/proposals_ai.py) against a real Postgres, read back
through the endpoints the pages call.

    TRACEIQ_SCRATCH_DB=traceiq_gseed_prop ./run-tests-live.sh tests/integration/test_demo_seed_proposals_db.py -q
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest
from sqlmodel import select

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.core.auth import AuthPrincipal                              # noqa: E402
from app.models import (                                             # noqa: E402
    BenchmarkGroup, CaseProposal, FlakeRecord, HypothesisFeedback, TestCaseResult, TestCaseRevision,
    TestRun,
)
from app.services import demo_seed, demo_workspace as dw              # noqa: E402
from tests.integration.demo_seed_fixtures import (                   # noqa: E402,F401
    KEEP_AFTER_DELETE, count_all_tables, owner, session,
)


@pytest.fixture(autouse=True)
def only_this_module(monkeypatch):
    monkeypatch.setattr(demo_seed, "MODULES", ("proposals_ai",))


def _principal(user):
    return AuthPrincipal(user=user, api_key=None)


async def test_proposals_and_investigation_reach_their_pages(session, owner):
    out = await dw.create_demo_workspace(session, owner)
    web, api = out["projects"][0]["id"], out["projects"][1]["id"]
    p = _principal(owner)

    # Review queue: pending update (with rationale/agent/confidence), create, delete.
    from app.api import agent_ownership, heal_proposals
    pending = (await agent_ownership.list_proposals(project_id=web, status="pending", limit=100,
                                                    principal=p, session=session)
               + await agent_ownership.list_proposals(project_id=api, status="pending", limit=100,
                                                      principal=p, session=session))
    assert {x.action for x in pending} == {"create", "update", "delete"}
    upd = next(x for x in pending if x.action == "update")
    assert upd.rationale and upd.agent_id and upd.ai_confidence > 0.5
    rows = (await session.exec(select(CaseProposal).where(CaseProposal.project_id.in_([web, api])))).all()
    assert all(r.created_by_id == owner.id for r in rows)
    assert {r.status for r in rows} == {"pending", "accepted", "rejected"}
    assert any(r.status == "accepted" and r.decided_by_id is None
               and r.decision_note.startswith("auto-applied") for r in rows)
    heals = await heal_proposals.list_proposals(status="pending", test_case_id=None, project_id=web,
                                                principal=p, session=session)
    assert len(heals) == 1 and heals[0].candidates and heals[0].new_selector != heals[0].old_selector

    # Applied proposals leave revisions; the accepted UPDATE really changed the case.
    revs = (await session.exec(select(TestCaseRevision))).all()
    assert {r.change_source for r in revs} == {"create", "proposal"}

    # Step evidence: Flake trends' failing step types, Selector health.
    from app.api import frontend_quality, reliability_reports
    patterns = await reliability_reports.flake_patterns_report(web, days=30, principal=p, session=session)
    types = {b.get("step_type") or b.get("type") or b.get("key") for b in patterns["by_failing_step_type"]}
    assert types - {None, "unknown"}, patterns["by_failing_step_type"]
    health = await frontend_quality.selector_health(web, days=90, session=session, principal=p)
    assert health["totals"]["failures"] > 0 and health["totals"]["heals"] == 1
    assert any(e.get("suggestions") for e in health["entries"])

    # Failure analysis v2 on a failed run + reviewer votes.
    analysed = [r for r in (await session.exec(select(TestRun).where(
        TestRun.project_id.in_([web, api])))).all() if r.ai_analysis]
    assert analysed
    report = analysed[0].ai_analysis["reports"][0]
    assert report["schema_version"] == 2 and report["hypotheses"] and report["observed"]
    assert report["analyzed_by"] == "heuristic"
    from app.api import value_report
    fb = await value_report.run_hypothesis_feedback(analysed[0].id, session=session, principal=p)
    assert any(row["correct"] or row["incorrect"] for rows_ in fb["results"].values() for row in rows_)
    votes = (await session.exec(select(HypothesisFeedback))).all()
    assert len({(v.result_id, v.hypothesis_key) for v in votes if v.hypothesis_index == 0}) >= 5
    assert {v.verdict for v in votes} == {"correct", "incorrect"}

    # Value report: AI accuracy, targeted-run savings, proposal outcomes.
    vr = await value_report.project_value_report(web, start=None, end=None, session=session, principal=p)
    metrics = {m["key"]: m for m in vr["metrics"]}
    assert metrics["targeted_time_saved"]["value"] is not None, metrics["targeted_time_saved"]
    assert metrics["proposal_outcomes"]["value"] is not None, metrics["proposal_outcomes"]
    wvr = await value_report.workspace_value_report(out["workspace_id"], start=None, end=None,
                                                    project_ids=None, session=session, principal=p)
    accuracy = {m["key"]: m for m in wvr["metrics"]}["analysis_accuracy"]
    assert accuracy["value"] is not None, accuracy

    # Flags + catalogue; benchmark matrix.
    from app.api import benchmarks, journey_reports
    cov = await journey_reports.flag_coverage(web, days=30, session=session, principal=p)
    text = json.dumps(cov, default=str)
    assert "checkout_v2" in text and "holdout" in text
    group = (await session.exec(select(BenchmarkGroup))).one()
    g = await benchmarks.get_group(group.id, session=session, principal=p)
    assert len(g["matrix"]["sites"]) == 2 and all(s["run_id"] for s in g["matrix"]["sites"])

    flaky = [r for r in (await session.exec(select(TestCaseResult).join(
        TestRun, TestRun.id == TestCaseResult.test_run_id).where(TestRun.project_id == web))).all()
        if r.is_flaky]
    assert flaky and all(r.result_payload["attempts"][0]["status"] == "failed" for r in flaky)

    # The flake record reports no more samples than the real scorer keeps.
    assert all(f.sample_count <= 20 for f in (await session.exec(select(FlakeRecord))).all())
    # New runs never fail (every demo failure belongs to a cluster).
    failed = (await session.exec(select(TestCaseResult).join(TestRun, TestRun.id == TestCaseResult.test_run_id)
                                 .where(TestRun.project_id.in_([web, api]),
                                        TestCaseResult.status == "FAILED"))).all()
    assert all(r.cluster_id for r in failed)


async def test_delete_removes_everything_this_module_adds(session, owner, monkeypatch):
    from app.core import storage
    monkeypatch.setattr(storage.minio_client, "delete_prefix", lambda p: 0)
    monkeypatch.setattr(storage.minio_client, "delete_object", lambda k: None)
    owner_id = owner.id
    before = await count_all_tables(session)
    await dw.create_demo_workspace(session, owner)
    assert await dw.delete_demo_workspace(session, SimpleNamespace(id=owner_id))
    session.expire_all()
    after = await count_all_tables(session)
    leftovers = {t: after[t] - before[t] for t in after
                 if after[t] != before[t] and t not in KEEP_AFTER_DELETE}
    assert not leftovers, leftovers
