"""WP12c reporting against a real Postgres: analytics import + coverage,
business journey board, flags on runs, benchmark dispatch, access control.

    TRACEIQ_SCRATCH_DB=traceiq_wp12c ./run-tests-live.sh tests/integration/test_journey_reports_db.py -q
"""
from __future__ import annotations

import io
import json
import os
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException, UploadFile
from sqlmodel import select

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api import benchmarks as bapi                          # noqa: E402
from app.api import journey_reports as jr                       # noqa: E402
from app.api.endpoints import test_runs                         # noqa: E402
from app.core.auth import AuthPrincipal                         # noqa: E402
from app.models import (                                        # noqa: E402
    ApiKey, BenchmarkGroup, FlakeRecord, JourneyImport, PageBudgetSample, Project,
    RequirementLink, TestCase, TestCaseResult, TestRun, TestStatus, User, UserProjectAccess,
)

from tests.integration.test_targeted_runs_db import (           # noqa: E402,F401
    no_queue, scene, session,
)
from tests.integration.denied import DENIED  # noqa: E402


def me(scene, key=None):
    return AuthPrincipal(user=scene.user, api_key=key)


async def _user(session, scene, email, level=None):
    u = User(email=email, full_name=email.split("@")[0], hashed_password="x", is_verified=True)
    session.add(u)
    await session.flush()
    if level:
        session.add(UserProjectAccess(user_id=u.id, project_id=scene.project_id, access_level=level))
    await session.commit()
    return AuthPrincipal(user=u, api_key=None)


def _upload(data: bytes, name="ga.csv") -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=name)


async def _steps(session, case_id, *urls):
    c = await session.get(TestCase, case_id)
    c.steps = [{"type": "goto", "value": u} for u in urls]
    session.add(c)
    await session.commit()


CSV = (b"path,views,journey,step\n"
       b"/,500,,\n/product/1?ref=x,200,,\n/product/2/,100,,\n/about,150,,\n/legal,50,,\n"
       b"/cart,80,Checkout,1\n/pay,60,Checkout,2\n")


# --- import + coverage ------------------------------------------------------------

async def test_import_versions_and_traffic_weighted_coverage(session, scene):
    await _steps(session, scene.root_cases[0], "{{env.BASE}}/product/9", "/cart")
    await _steps(session, scene.root_cases[1], "https://shop.example.com/legal")
    # Another project's case visiting "/" must not count as coverage here.
    await _steps(session, scene.foreign_case, "/")

    first = await jr.create_import(scene.project_id, None, file=_upload(CSV), label="Sept",
                                   format=None, session=session, principal=me(scene))
    assert first["total_views"] == 1000 and first["journeys"] == ["Checkout"]
    second = await jr.create_import(scene.project_id, None, file=_upload(CSV), label="Oct",
                                    format=None, session=session, principal=me(scene))
    listed = await jr.list_imports(scene.project_id, session=session, principal=me(scene))
    assert [i["id"] for i in listed["imports"]] == [second["id"], first["id"]]

    out = await jr.journey_coverage(scene.project_id, import_id=None, top=50,
                                    session=session, principal=me(scene))
    assert out["import"]["id"] == second["id"]
    rep = out["report"]
    # Without rules /product/1 and /product/2 are separate keys, neither tested.
    assert rep["covered_views"] == 50 and rep["coverage_pct"] == 5.0

    await jr.put_rules(scene.project_id, jr.RulesIn(path_rules=[{"pattern": "/product/{id}"}]),
                       None, session=session, principal=me(scene))
    rep = (await jr.journey_coverage(scene.project_id, import_id=None, top=50,
                                     session=session, principal=me(scene)))["report"]
    assert rep["covered_views"] == 350 and rep["coverage_pct"] == 35.0
    assert rep["top_uncovered"][0]["key"] == "/"
    (checkout,) = rep["journeys"]
    assert checkout["covered_steps"] == 1 and checkout["first_gap"] == "/pay"

    # Old version is still reachable by id.
    old = await jr.journey_coverage(scene.project_id, import_id=first["id"], top=5,
                                    session=session, principal=me(scene))
    assert old["import"]["label"] == "Sept"


async def test_import_rejects_bad_files_and_prunes_history(session, scene, monkeypatch):
    with pytest.raises(HTTPException) as exc:
        await jr.create_import(scene.project_id, None, file=_upload(b"views\n1\n"), label=None,
                               format=None, session=session, principal=me(scene))
    assert exc.value.status_code == 400 and "path column" in exc.value.detail
    monkeypatch.setattr(jr.jc, "MAX_IMPORTS_KEPT", 2)
    for _ in range(3):
        await jr.create_import(scene.project_id, None,
                               file=_upload(json.dumps([{"url": "/a", "views": 1}]).encode(), "x.json"),
                               label=None, format=None, session=session, principal=me(scene))
    assert len((await session.exec(select(JourneyImport).where(
        JourneyImport.project_id == scene.project_id))).all()) == 2
    with pytest.raises(HTTPException) as exc:
        await jr.put_rules(scene.project_id, jr.RulesIn(path_rules=[{"pattern": "(?=x)", "kind": "regex"}]),
                           None, session=session, principal=me(scene))
    assert exc.value.status_code == 400


async def test_access_control_on_imports_rules_and_cross_project_ids(session, scene):
    viewer = await _user(session, scene, "viewer@corp.example.com", "viewer")
    outsider = await _user(session, scene, "out@else.example.com")
    for call in (
        lambda p: jr.create_import(scene.project_id, None, file=_upload(CSV), label=None, format=None,
                                   session=session, principal=p),
        lambda p: jr.put_rules(scene.project_id, jr.RulesIn(), None, session=session, principal=p),
        lambda p: jr.create_flag(scene.project_id, jr.FlagIn(name="x"), None, session=session, principal=p),
        lambda p: jr.create_journey(scene.project_id, jr.JourneyIn(name="J"), None,
                                    session=session, principal=p),
    ):
        for p in (viewer, outsider):
            with pytest.raises(HTTPException) as exc:
                await call(p)
            assert exc.value.status_code in DENIED
    # Viewers can read; outsiders cannot.
    await jr.journey_coverage(scene.project_id, import_id=None, top=5, session=session, principal=viewer)
    with pytest.raises(HTTPException) as exc:
        await jr.journey_board(scene.project_id, session=session, principal=outsider)
    assert exc.value.status_code in DENIED
    # An import of another project is not reachable through this project.
    other = await jr.create_import(scene.other_project_id, None, file=_upload(CSV), label=None,
                                   format=None, session=session, principal=me(scene))
    with pytest.raises(HTTPException) as exc:
        await jr.journey_coverage(scene.project_id, import_id=other["id"], top=5,
                                  session=session, principal=me(scene))
    assert exc.value.status_code == 404
    # A key scoped to the other project cannot read this one.
    key = ApiKey(name="ci", workspace_id=1, project_id=scene.other_project_id, prefix="tiq_x", hashed_key="h")
    with pytest.raises(HTTPException) as exc:
        await jr.flag_coverage(scene.project_id, days=30, session=session, principal=me(scene, key))
    assert exc.value.status_code in DENIED


# --- business journeys ----------------------------------------------------------------

async def _result(session, scene, case_id, status, *, days_ago=0, flaky=False, project_id=None):
    run = TestRun(test_suite_id=scene.root_id, project_id=project_id or scene.project_id,
                  status=status, created_at=datetime.utcnow() - timedelta(days=days_ago, minutes=1))
    session.add(run)
    await session.flush()
    session.add(TestCaseResult(test_run_id=run.id, test_name="x", status=status, duration_ms=1,
                               test_case_id=case_id, is_flaky=flaky))
    await session.commit()
    return run


async def test_journey_board_health_requirements_and_traffic(session, scene):
    cart, address = scene.root_cases
    card, upi = scene.child_cases
    await _steps(session, cart, "/cart")
    await jr.create_import(scene.project_id, None, file=_upload(CSV), label=None, format=None,
                           session=session, principal=me(scene))
    # Validation: foreign case, bad criticality, owner without access.
    for body in (jr.JourneyIn(name="X", case_ids=[scene.foreign_case]),
                 jr.JourneyIn(name="X", criticality="urgent")):
        with pytest.raises(HTTPException) as exc:
            await jr.create_journey(scene.project_id, body, None, session=session, principal=me(scene))
        assert exc.value.status_code == 400
    stranger = await _user(session, scene, "stranger@else.example.com")
    with pytest.raises(HTTPException) as exc:
        await jr.create_journey(scene.project_id, jr.JourneyIn(name="X", owner_id=stranger.user.id),
                                None, session=session, principal=me(scene))
    assert exc.value.status_code == 400

    checkout = await jr.create_journey(scene.project_id, jr.JourneyIn(
        name="Checkout", criticality="critical", case_ids=[cart, card], owner_id=scene.user.id,
        requirement_refs=["REQ-1", "REQ-2"], traffic_journey="Checkout"),
        None, session=session, principal=me(scene))
    signup = await jr.create_journey(scene.project_id, jr.JourneyIn(name="Signup", case_ids=[address, upi]),
                                     None, session=session, principal=me(scene))
    with pytest.raises(HTTPException) as exc:
        await jr.create_journey(scene.project_id, jr.JourneyIn(name="Checkout"), None,
                                session=session, principal=me(scene))
    assert exc.value.status_code == 409

    await _result(session, scene, cart, TestStatus.PASSED, days_ago=1)
    await _result(session, scene, cart, TestStatus.PASSED)
    await _result(session, scene, card, TestStatus.FAILED, days_ago=2)
    await _result(session, scene, card, TestStatus.PASSED)
    await _result(session, scene, address, TestStatus.FAILED)
    # A result recorded under ANOTHER project's run is not this project's history.
    await _result(session, scene, upi, TestStatus.PASSED, project_id=scene.other_project_id)
    session.add(RequirementLink(test_case_id=cart, project_id=scene.project_id, ref="REQ-1"))
    session.add(RequirementLink(test_case_id=card, project_id=scene.project_id, ref="REQ-3"))
    await session.commit()

    board = {j["name"]: j for j in (await jr.journey_board(
        scene.project_id, session=session, principal=me(scene)))["journeys"]}
    c = board["Checkout"]
    assert c["health"] == "flaky"            # card passed last after a failure
    assert c["owner"]["email"] == scene.user.email and c["last_run_at"]
    assert {r["ref"]: (r["cases"], r["status"]) for r in c["requirements"]} == {
        "REQ-1": (1, "passing"), "REQ-2": (0, "uncovered"), "REQ-3": (1, "flaky")}
    assert c["requirements_covered"] == 2
    assert c["traffic"]["source"] == "journey" and c["traffic"]["views"] == 140
    assert c["traffic"]["covered_steps"] == 1
    assert c["trend"][-1]["passed"] == 2
    s = board["Signup"]
    assert s["health"] == "failing" and s["counts"]["not_run"] == 1
    assert s["traffic"]["source"] == "pages"

    # Quarantine makes a passing case flaky; deleting a case just drops it.
    session.add(FlakeRecord(test_case_id=cart, is_quarantined=True))
    await session.commit()
    await jr.update_journey(signup["id"], jr.JourneyPatch(case_ids=[cart]), None,
                            session=session, principal=me(scene))
    board = {j["name"]: j for j in (await jr.journey_board(
        scene.project_id, session=session, principal=me(scene)))["journeys"]}
    assert board["Signup"]["health"] == "flaky"

    viewer = await _user(session, scene, "v2@corp.example.com", "viewer")
    with pytest.raises(HTTPException) as exc:
        await jr.delete_journey(checkout["id"], None, session=session, principal=viewer)
    assert exc.value.status_code == 403
    await jr.delete_journey(checkout["id"], None, session=session, principal=me(scene))


# --- flags ------------------------------------------------------------------------------

async def test_flags_are_stored_on_runs_and_reported(session, scene):
    runs = await test_runs.create_run(
        suite_id=scene.root_id, case_id=None, browser=["chromium"], device=None, tags=None,
        context=test_runs.RunCreateContext(case_ids=[scene.root_cases[0]],
                                           flags={"checkout_v2": True, "exp": "B"}),
        session=session, principal=me(scene))
    assert runs[0].flags == {"checkout_v2": True, "exp": "B"}
    stored = await session.get(TestRun, runs[0].id)
    assert stored.flags == {"checkout_v2": True, "exp": "B"}
    with pytest.raises(HTTPException) as exc:
        await test_runs.create_run(
            suite_id=scene.root_id, case_id=None, browser=["chromium"], device=None, tags=None,
            context=test_runs.RunCreateContext(flags={"bad name": 1}), session=session, principal=me(scene))
    assert exc.value.status_code == 400
    # A run with no flags stays NULL and is not counted.
    plain = await test_runs.create_run(
        suite_id=scene.sibling_id, case_id=None, browser=["chromium"], device=None, tags=None,
        context=None, session=session, principal=me(scene))
    assert plain[0].flags is None
    # A flagged run in another project is not this project's coverage.
    session.add(TestRun(test_suite_id=scene.root_id, project_id=scene.other_project_id,
                        flags={"exp": "C"}))
    await session.commit()

    await jr.create_flag(scene.project_id, jr.FlagIn(name="exp", values=["A", "B", "C"]), None,
                         session=session, principal=me(scene))
    with pytest.raises(HTTPException) as exc:
        await jr.create_flag(scene.project_id, jr.FlagIn(name="exp"), None, session=session, principal=me(scene))
    assert exc.value.status_code == 409
    rep = await jr.flag_coverage(scene.project_id, days=30, session=session, principal=me(scene))
    assert rep["runs_with_flags"] == 1
    by = {f["name"]: f for f in rep["flags"]}
    assert by["exp"]["never_tested"] == ["A", "C"]
    assert by["exp"]["values"][0]["cases"] == 1
    assert rep["uncatalogued"] == ["checkout_v2"]


def test_dispatch_payload_carries_flags_and_target_base_url():
    from app.worker import _settings_payload
    payload = _settings_payload({"flags": {"a": True}, "environment": {"name": "t", "base_url": "https://x.io",
                                                                       "variables": {}}})
    assert payload["flags"] == {"a": True}


# --- benchmarks --------------------------------------------------------------------------

@pytest.fixture
def public_dns(monkeypatch):
    async def ok(url, **kw):
        return url
    monkeypatch.setattr("app.core.net_guard.validate_outbound_url", ok)


async def test_benchmark_dispatches_one_run_per_target_and_builds_the_matrix(session, scene, public_dns,
                                                                             monkeypatch):
    queued = []
    from app import worker
    monkeypatch.setattr(worker.run_test_suite, "delay", lambda run_id, **kw: queued.append(run_id))
    body = bapi.BenchmarkIn(name="Home pages", test_suite_id=scene.root_id,
                            targets=["https://us.example.com", {"label": "Rival", "base_url": "https://rival.io/"}])
    group = await bapi.create_group(scene.project_id, body, None, session=session, principal=me(scene))
    runs = (await session.exec(select(TestRun).where(TestRun.benchmark_group_id == group["id"])
                               .order_by(TestRun.id))).all()
    assert [r.target_url for r in runs] == ["https://us.example.com", "https://rival.io"]
    assert all(r.test_case_id is None and r.test_suite_id == scene.root_id for r in runs)
    assert sorted(queued) == sorted(r.id for r in runs)

    session.add_all([
        TestCaseResult(test_run_id=runs[0].id, test_name="cart", status=TestStatus.PASSED, duration_ms=100,
                       test_case_id=scene.root_cases[0]),
        TestCaseResult(test_run_id=runs[1].id, test_name="cart", status=TestStatus.FAILED, duration_ms=300,
                       test_case_id=scene.root_cases[0]),
        PageBudgetSample(project_id=scene.project_id, test_run_id=runs[1].id, page_url="https://rival.io/",
                         page_key="https://rival.io/", metrics={"lcp_ms": 2400}),
    ])
    runs[0].status = TestStatus.PASSED
    runs[1].status = TestStatus.FAILED
    session.add_all(runs)
    await session.commit()
    out = await bapi.get_group(group["id"], session=session, principal=me(scene))
    us, rival = out["matrix"]["sites"]
    assert us["pass_rate"] == 100.0 and rival["pass_rate"] == 0.0
    assert rival["budgets"] == {"lcp_ms": 2400.0} and out["matrix"]["summary"]["complete"]
    assert out["matrix"]["tests"][0]["differs"]

    again = await bapi.rerun_group(group["id"], session=session, principal=me(scene))
    assert again["id"] != group["id"]
    listed = await bapi.list_groups(scene.project_id, limit=50, session=session, principal=me(scene))
    assert [g["id"] for g in listed["groups"]] == [again["id"], group["id"]]

    await bapi.delete_group(group["id"], None, session=session, principal=me(scene))
    assert (await session.get(TestRun, runs[0].id)).benchmark_group_id is None


async def test_benchmark_validation_policy_and_access(session, scene, public_dns):
    def body(**kw):
        return bapi.BenchmarkIn(**{"name": "B", "test_suite_id": scene.root_id,
                                   "targets": ["https://a.io", "https://b.io"], **kw})
    for b in (body(test_suite_id=scene.sibling_id + 1000), body(targets=["https://a.io"]),
              body(browser="ie6"), body(targets=["file:///etc/passwd", "https://b.io"])):
        with pytest.raises(HTTPException) as exc:
            await bapi.create_group(scene.project_id, b, None, session=session, principal=me(scene))
        assert exc.value.status_code == 400
    # Enforced egress allowlist: a target off the list is refused.
    project = await session.get(Project, scene.project_id)
    project.network_policy = {"enforce": True, "allowed_hosts": ["a.io"]}
    session.add(project)
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await bapi.create_group(scene.project_id, body(), None, session=session, principal=me(scene))
    assert exc.value.status_code == 400 and "allowlist" in exc.value.detail
    project.network_policy = None
    session.add(project)
    await session.commit()

    viewer = await _user(session, scene, "viewer@corp.example.com", "viewer")
    with pytest.raises(HTTPException) as exc:
        await bapi.create_group(scene.project_id, body(), None, session=session, principal=viewer)
    assert exc.value.status_code == 403
    # A suite of another project cannot be benchmarked from this one.
    from app.models import TestSuite
    foreign = (await session.exec(select(TestSuite).where(
        TestSuite.project_id == scene.other_project_id))).first()
    with pytest.raises(HTTPException) as exc:
        await bapi.create_group(scene.project_id, body(test_suite_id=foreign.id), None,
                                session=session, principal=me(scene))
    assert exc.value.status_code == 400
    group = await bapi.create_group(scene.project_id, body(), None, session=session, principal=me(scene))
    outsider = await _user(session, scene, "out@else.example.com")
    with pytest.raises(HTTPException) as exc:
        await bapi.get_group(group["id"], session=session, principal=outsider)
    assert exc.value.status_code == 403
    # Runs of another project carrying the group id never join the matrix.
    session.add(TestRun(test_suite_id=foreign.id, project_id=scene.other_project_id,
                        benchmark_group_id=group["id"], target_url="https://a.io"))
    await session.commit()
    out = await bapi.get_group(group["id"], session=session, principal=me(scene))
    assert len(out["run_ids"]) == 2
