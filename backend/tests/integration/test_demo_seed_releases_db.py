"""Demo extras — delivery, incidents, dependencies, journeys, releases
(app/services/demo_seed/releases_reports.py) against a real Postgres, read back
through the endpoints the pages call.

    TRACEIQ_SCRATCH_DB=traceiq_gseed_rel ./run-tests-live.sh tests/integration/test_demo_seed_releases_db.py -q
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from sqlmodel import select

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.core.auth import AuthPrincipal                              # noqa: E402
from app.services import demo_seed                                   # noqa: E402
from app.services import demo_workspace as dw                        # noqa: E402
from tests.integration.demo_seed_fixtures import (                   # noqa: E402,F401
    KEEP_AFTER_DELETE, count_all_tables, demo, owner, session,
)
# Re-run the shared "nothing dated in the future" check with only this area.
from tests.integration.test_demo_seed_db import (                    # noqa: E402,F401
    test_nothing_seeded_is_dated_after_creation,
)


@pytest.fixture(autouse=True)
def _only_this_area(monkeypatch):
    monkeypatch.setattr(demo_seed, "MODULES", ("releases_reports",))


async def _demo(session, owner):
    out = await dw.create_demo_workspace(session, owner)
    return out, out["projects"][0]["id"], out["projects"][1]["id"]


async def test_delivery_incidents_and_dependencies(session, owner):
    from app.api import dora, incidents, reliability_reports
    _out, web, api = await _demo(session, owner)
    p = AuthPrincipal(user=owner, api_key=None)

    from app.models import Deployment
    rows = (await session.exec(select(Deployment).where(Deployment.project_id == web))).all()
    assert rows and all(d.git_commit and d.commit_at and d.commit_at < d.deployed_at for d in rows)
    assert sorted(d.version for d in rows)[-1] == "2.4.0"

    metrics = await dora.dora_metrics(web, days=30, environment_id=None, principal=p, session=session)
    assert metrics["lead_time"]["median_hours"] is not None, metrics["lead_time"]
    assert metrics["time_to_restore"]["median_hours"] is not None, metrics["time_to_restore"]
    assert metrics["change_failure_rate"]["linked_incidents"] >= 2

    web_inc = await incidents.list_incidents(web, status=None, escaped=None, limit=200,
                                             principal=p, session=session)
    api_inc = await incidents.list_incidents(api, status=None, escaped=None, limit=200,
                                             principal=p, session=session)
    assert len(web_inc) == 4 and len(api_inc) == 1
    recurring = [i for i in web_inc if i["recurrence_count"] == 1]
    assert len(recurring) == 1 and recurring[0]["previous_incident_id"]
    escaped = [i for i in web_inc if i["escaped_defect"]]
    assert len(escaped) == 1 and escaped[0]["regression_case_id"] and escaped[0]["escaped_case_id"]
    assert api_inc[0]["matched_cluster_id"] and api_inc[0]["matched_case_id"]
    assert any(i["matched_case_id"] for i in web_inc if i["status"] == "acknowledged")
    report = await incidents.escaped_report(web, days=180, period="month", principal=p, session=session)
    assert "escaped" in repr(report)

    health = await reliability_reports.dependency_health(web, days=30, principal=p, session=session)
    names = {v["vendor"] for v in health["vendors"]}
    assert {"stripe.com", "googleapis.com"} <= names, names
    stripe = next(v for v in health["vendors"] if v["vendor"] == "stripe.com")
    assert stripe["errors"] > 0


async def test_journeys_releases_and_requirements(session, owner):
    from app.api import journey_reports, manual_testing, requirements
    from app.api.endpoints import quality
    from app.models import ExternalTestReport, FlakeRecord, GatePolicyVersion
    from app.services import flake_policy
    _out, web, _api = await _demo(session, owner)
    p = AuthPrincipal(user=owner, api_key=None)

    cov = await journey_reports.journey_coverage(web, import_id=None, top=50, session=session, principal=p)
    text = repr(cov)
    assert "/signup" in text and "/login" in text and "Sign up" in text
    board = await journey_reports.journey_board(web, session=session, principal=p)
    assert "Sign in and manage todos" in repr(board)

    signoffs = await manual_testing.list_signoffs(web, limit=50, session=session, principal=p)
    assert sorted(s["verdict"] for s in signoffs) == ["approve", "reject"]
    assert {s["release_label"] for s in signoffs} == {"2.4.0"}
    runs = await manual_testing.list_manual_runs(web, limit=50, session=session, principal=p)
    assert len(runs) == 2

    assert (await session.exec(select(GatePolicyVersion).where(GatePolicyVersion.project_id == web))).all()
    assert len((await session.exec(select(ExternalTestReport).where(
        ExternalTestReport.project_id == web))).all()) == 2
    flakes = (await session.exec(select(FlakeRecord))).all()
    assert any(flake_policy.is_active(f) for f in flakes)

    readiness = await quality.release_readiness_view(web, git_commit=None, git_branch=None,
                                                     session=session, principal=p)
    verdict = readiness.verdict if hasattr(readiness, "verdict") else readiness["verdict"]
    assert verdict != "ready"

    listed = await requirements.list_imported(web, principal=p, session=session)
    states = {r["external_key"]: r["coverage"]["status"] for r in listed["requirements"]}
    assert states["DEMO-101"] in ("covered", "failing")
    assert states["DEMO-103"] == "untested"
    assert states["DEMO-104"] == "failing"
    assert states["DEMO-105"] == "not_covered"
    by_key = {r["external_key"]: r for r in listed["requirements"]}
    assert by_key["DEMO-102"]["ambiguous_count"] >= 1
    assert by_key["DEMO-101"]["criteria_count"] >= 2
    assert by_key["DEMO-105"]["proposed_count"] == 1
    detail = await requirements.get_requirement(by_key["DEMO-105"]["id"], principal=p, session=session)
    assert any(c.get("proposal_status") == "pending" for c in detail["acceptance_criteria"])


async def test_delete_removes_everything_this_area_adds(session, owner, monkeypatch):
    from app.core import storage
    monkeypatch.setattr(storage.minio_client, "delete_prefix", lambda p: 0)
    monkeypatch.setattr(storage.minio_client, "delete_object", lambda k: None)
    owner_id = owner.id
    before = await count_all_tables(session)
    await dw.create_demo_workspace(session, owner)
    during = await count_all_tables(session)
    for table in ("incident", "vendorhealthdaily", "journeyimport", "businessjourney", "releasesignoff",
                  "externaltestreport", "gatepolicyversion", "caseproposal"):
        assert during[table] > before[table], table
    assert await dw.delete_demo_workspace(session, SimpleNamespace(id=owner_id))
    session.expire_all()
    after = await count_all_tables(session)
    leftovers = {t: after[t] - before[t] for t in after
                 if after[t] != before[t] and t not in KEEP_AFTER_DELETE}
    assert not leftovers, leftovers
