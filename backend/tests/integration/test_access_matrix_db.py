"""Identity-matrix runs + access-control matrix against a real Postgres.

    TRACEIQ_SCRATCH_DB=traceiq_wp4 ./run-tests-live.sh tests/integration/test_access_matrix_db.py -q
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import Session, create_engine, select

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api import access_matrix as api                       # noqa: E402
from app.core.auth import AuthPrincipal                        # noqa: E402
from app.core.config import db_url_for, settings               # noqa: E402
from app.models import (                                       # noqa: E402
    AccessMatrixCell, AccessMatrixRun, Persona, Project, SecurityFinding, Tenant,
    TestRun, User, UserProjectAccess, Workspace,
)
from app.tasks import access_matrix_tasks as tasks             # noqa: E402

from tests.integration.test_targeted_runs_db import (          # noqa: E402,F401
    no_queue, scene, session,
)
from app.api.endpoints import test_runs                        # noqa: E402
from tests.integration.denied import DENIED  # noqa: E402

REQ = SimpleNamespace(headers={}, client=None)


@pytest.fixture(autouse=True)
def no_matrix_queue(monkeypatch):
    monkeypatch.setattr(tasks.dispatch_access_matrix_run, "delay", lambda *a, **kw: None)


def _me(scene):
    return AuthPrincipal(user=scene.user, api_key=None)


async def _personas(session, scene, n=2, project_id=None):
    project = await session.get(Project, scene.project_id)
    out = []
    for i in range(n):
        p = Persona(workspace_id=project.workspace_id, project_id=project_id or scene.project_id,
                    name=f"user{i}", auth_headers={"X-User": f"u{i}"})
        session.add(p)
        out.append(p)
    await session.commit()
    return out


async def _enable_security(session, scene, domains=("app.example.com",)):
    project = await session.get(Project, scene.project_id)
    project.security_settings = {"enabled": True, "allowed_domains": list(domains)}
    session.add(project)
    await session.commit()


# --- persona_ids fan-out -------------------------------------------------------

async def test_persona_ids_fan_out_one_run_per_identity(session, scene):
    a, b = await _personas(session, scene)
    runs = await test_runs.create_run(
        suite_id=scene.root_id, case_id=scene.root_cases[0], browser=["chromium"], device=None,
        tags=None, context=test_runs.RunCreateContext(persona_ids=[a.id, b.id], include_anonymous=True),
        session=session, principal=_me(scene))
    assert len(runs) == 3
    assert [r.persona_id for r in runs] == [a.id, b.id, None]
    assert [bool(r.anonymous) for r in runs] == [False, False, True]
    assert len({r.matrix_group for r in runs}) == 1 and runs[0].matrix_group


async def test_persona_ids_with_targeted_cases_multiplies(session, scene):
    a, b = await _personas(session, scene)
    runs = await test_runs.create_run(
        suite_id=scene.root_id, case_id=None, browser=["chromium"], device=None, tags=None,
        context=test_runs.RunCreateContext(case_ids=scene.root_cases, persona_ids=[a.id, b.id]),
        session=session, principal=_me(scene))
    assert len(runs) == 4
    assert sorted((r.test_case_id, r.persona_id) for r in runs) == sorted(
        (c, p) for c in scene.root_cases for p in (a.id, b.id))


async def test_plain_run_has_no_matrix_group(session, scene):
    runs = await test_runs.create_run(
        suite_id=scene.root_id, case_id=scene.root_cases[0], browser=["chromium"], device=None,
        tags=None, context=test_runs.RunCreateContext(), session=session, principal=_me(scene))
    assert runs[0].matrix_group is None and runs[0].anonymous is None


async def test_foreign_persona_is_refused(session, scene):
    other_user = User(email="o@else.example.com", full_name="O", hashed_password="x")
    session.add(other_user)
    await session.flush()
    t = Tenant(name="Else", owner_id=other_user.id)
    session.add(t)
    await session.flush()
    ws = Workspace(name="Else ws", tenant_id=t.id)
    session.add(ws)
    await session.flush()
    foreign = Persona(workspace_id=ws.id, name="theirs")
    other_project = Persona(workspace_id=(await session.get(Project, scene.project_id)).workspace_id,
                            project_id=scene.other_project_id, name="other-project")
    session.add_all([foreign, other_project])
    await session.commit()
    (mine,) = await _personas(session, scene, n=1)
    for bad in (foreign.id, other_project.id):
        with pytest.raises(HTTPException) as exc:
            await test_runs.create_run(
                suite_id=scene.root_id, case_id=scene.root_cases[0], browser=["chromium"],
                device=None, tags=None,
                context=test_runs.RunCreateContext(persona_ids=[mine.id, bad]),
                session=session, principal=_me(scene))
        assert exc.value.status_code == 400
    assert (await session.exec(select(TestRun))).all() == []


async def test_persona_id_and_persona_ids_are_exclusive(session, scene):
    a, b = await _personas(session, scene)
    with pytest.raises(HTTPException) as exc:
        await test_runs.create_run(
            suite_id=scene.root_id, case_id=scene.root_cases[0], browser=["chromium"], device=None,
            tags=None, context=test_runs.RunCreateContext(persona_id=a.id, persona_ids=[b.id]),
            session=session, principal=_me(scene))
    assert exc.value.status_code == 400


# --- matrix CRUD access control ----------------------------------------------------

def _body(personas, rows, **kw):
    return api.AccessMatrixIn(name="authz", persona_ids=[p.id for p in personas], rows=rows,
                              base_url="https://app.example.com", **kw)


def _req_row(key, a, b, **kw):
    return {"key": key, "label": key, "kind": "request", "url": f"/api/{key}",
            "cells": {str(a.id): {"expect": "allow"}, str(b.id): {"expect": "deny"}}, **kw}


async def _viewer(session, scene):
    v = User(email="viewer@corp.example.com", full_name="V", hashed_password="x")
    session.add(v)
    await session.flush()
    session.add(UserProjectAccess(user_id=v.id, project_id=scene.project_id, access_level="viewer"))
    await session.commit()
    return AuthPrincipal(user=v, api_key=None)


async def test_viewer_reads_but_cannot_write_or_run(session, scene):
    a, b = await _personas(session, scene)
    m = await api.create_matrix(scene.project_id, _body([a, b], [_req_row("orders", a, b)]), REQ,
                                session=session, principal=_me(scene))
    viewer = await _viewer(session, scene)
    listed = await api.list_matrices(scene.project_id, session=session, principal=viewer)
    assert [x["id"] for x in listed] == [m["id"]]
    for call in (
        api.create_matrix(scene.project_id, _body([a, b], []), REQ, session=session, principal=viewer),
        api.update_matrix(m["id"], _body([a, b], []), REQ, session=session, principal=viewer),
        api.run_matrix(m["id"], REQ, api.MatrixRunRequest(), session=session, principal=viewer),
        api.delete_matrix(m["id"], REQ, session=session, principal=viewer),
    ):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 403


async def test_outsider_and_wrong_scope_key_are_refused(session, scene):
    a, b = await _personas(session, scene)
    m = await api.create_matrix(scene.project_id, _body([a, b], []), REQ,
                                session=session, principal=_me(scene))
    outsider = User(email="x@else.example.com", full_name="X", hashed_password="x")
    session.add(outsider)
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await api.get_matrix(m["id"], session=session, principal=AuthPrincipal(user=outsider, api_key=None))
    assert exc.value.status_code in DENIED
    key = SimpleNamespace(id=1, project_id=scene.other_project_id)
    with pytest.raises(HTTPException) as exc:
        await api.get_matrix(m["id"], session=session, principal=AuthPrincipal(user=scene.user, api_key=key))
    assert exc.value.status_code == 403


async def test_matrix_rejects_foreign_cases_and_personas(session, scene):
    a, b = await _personas(session, scene)
    rows = [{"key": "c", "kind": "case", "test_case_id": scene.foreign_case,
             "cells": {str(a.id): {"expect": "deny"}}}]
    with pytest.raises(HTTPException) as exc:
        await api.create_matrix(scene.project_id, _body([a, b], rows), REQ,
                                session=session, principal=_me(scene))
    assert exc.value.status_code == 400
    (other,) = await _personas(session, scene, n=1, project_id=scene.other_project_id)
    with pytest.raises(HTTPException) as exc:
        await api.create_matrix(scene.project_id, _body([a, other], []), REQ,
                                session=session, principal=_me(scene))
    assert exc.value.status_code == 400


# --- running a matrix -------------------------------------------------------------

async def test_run_is_refused_until_security_enabled_and_host_allowlisted(session, scene):
    a, b = await _personas(session, scene)
    m = await api.create_matrix(scene.project_id, _body([a, b], [_req_row("orders", a, b)]), REQ,
                                session=session, principal=_me(scene))
    with pytest.raises(HTTPException) as exc:
        await api.run_matrix(m["id"], REQ, api.MatrixRunRequest(), session=session, principal=_me(scene))
    assert exc.value.status_code == 403
    await _enable_security(session, scene, domains=("elsewhere.com",))
    with pytest.raises(HTTPException) as exc:
        await api.run_matrix(m["id"], REQ, api.MatrixRunRequest(), session=session, principal=_me(scene))
    assert exc.value.status_code == 403 and "allowlist" in exc.value.detail


async def test_mutating_rows_need_attestation(session, scene):
    a, b = await _personas(session, scene)
    await _enable_security(session, scene)
    m = await api.create_matrix(scene.project_id, _body([a, b], [_req_row("orders", a, b, method="DELETE")]),
                                REQ, session=session, principal=_me(scene))
    with pytest.raises(HTTPException) as exc:
        await api.run_matrix(m["id"], REQ, api.MatrixRunRequest(), session=session, principal=_me(scene))
    assert exc.value.status_code == 400 and "attest" in exc.value.detail
    out = await api.run_matrix(m["id"], REQ, api.MatrixRunRequest(authorized=True),
                               session=session, principal=_me(scene))
    assert out["authorized"] is True


class FakeRedis:
    def __init__(self):
        self.kv = {}

    def get(self, k):
        return self.kv.get(k)

    def delete(self, k):
        self.kv.pop(k, None)


def _sync():
    return create_engine(db_url_for(settings.DATABASE_URL, sync=True))


async def _execute(session, scene, matrix_id, observations, case_status=None):
    """Run the matrix and feed the collector fake worker observations by (row, persona)."""
    out = await api.run_matrix(matrix_id, REQ, api.MatrixRunRequest(), session=session,
                               principal=_me(scene))
    rc = FakeRedis()
    engine = _sync()
    with Session(engine) as s:
        cells = s.exec(select(AccessMatrixCell).where(AccessMatrixCell.matrix_run_id == out["id"])).all()
        for c in cells:
            obs = observations.get((c.row_key, c.persona_key))
            if obs is not None:
                rc.kv[f"access_check:result:{c.id}"] = json.dumps(obs)
            if c.test_run_id and case_status:
                run = s.get(TestRun, c.test_run_id)
                run.status, run.error_message = case_status
                s.add(run)
        s.commit()
        done = tasks.collect_once(s, rc, out["id"])
    engine.dispose()
    return out["id"], done


async def test_run_fans_out_cells_and_case_runs_and_records_findings(session, scene):
    a, b = await _personas(session, scene)
    await _enable_security(session, scene)
    rows = [
        _req_row("orders", a, b),
        {"key": "flow", "kind": "case", "test_case_id": scene.root_cases[0],
         "cells": {str(a.id): {"expect": "allow"}, str(b.id): {"expect": "deny"},
                   "anonymous": {"expect": "skip"}}},
    ]
    m = await api.create_matrix(scene.project_id, _body([a, b], rows, include_anonymous=True), REQ,
                                session=session, principal=_me(scene))
    obs = {("orders", str(a.id)): {"status": 200, "url": "https://app.example.com/api/orders",
                                   "headers": {"set-cookie": "sid=leak"}},
           ("orders", str(b.id)): {"status": 200, "url": "https://app.example.com/api/orders"}}
    run_id, done = await _execute(session, scene, m["id"], obs, case_status=("passed", None))
    assert done

    engine = _sync()
    with Session(engine) as s:
        mrun = s.get(AccessMatrixRun, run_id)
        cells = {(c.row_key, c.persona_key): c for c in s.exec(
            select(AccessMatrixCell).where(AccessMatrixCell.matrix_run_id == run_id)).all()}
        assert len(cells) == 4                       # skip cell not executed
        assert cells[("orders", str(a.id))].verdict == "pass"
        assert cells[("orders", str(a.id))].observed["headers"]["set-cookie"] == "[REDACTED]"
        assert cells[("orders", str(b.id))].verdict == "fail"
        assert cells[("flow", str(b.id))].verdict == "fail"   # journey completed as b
        runs = s.exec(select(TestRun).where(TestRun.matrix_group == mrun.matrix_group)).all()
        assert sorted(r.persona_id for r in runs) == sorted([a.id, b.id])
        assert mrun.status == "completed"
        assert mrun.summary["access_granted"] == 2 and mrun.summary["allow_regressions"] == 0
        findings = s.exec(select(SecurityFinding).where(
            SecurityFinding.project_id == scene.project_id)).all()
        assert len(findings) == 2
        assert {f.category for f in findings} == {"access_control"}
        assert {f.severity for f in findings} == {"high"}
    engine.dispose()


async def test_findings_dedupe_reopen_and_resolve_across_reruns(session, scene):
    a, b = await _personas(session, scene)
    await _enable_security(session, scene)
    m = await api.create_matrix(scene.project_id, _body([a, b], [_req_row("orders", a, b)]), REQ,
                                session=session, principal=_me(scene))
    granted = {("orders", str(a.id)): {"status": 200}, ("orders", str(b.id)): {"status": 200}}
    denied = {("orders", str(a.id)): {"status": 200}, ("orders", str(b.id)): {"status": 403}}
    timeout = {("orders", str(a.id)): {"status": 200},
               ("orders", str(b.id)): {"error_kind": "timeout", "error": "Timeout"}}

    def findings():
        engine = _sync()
        with Session(engine) as s:
            rows = s.exec(select(SecurityFinding).where(
                SecurityFinding.project_id == scene.project_id)).all()
        engine.dispose()
        return rows

    await _execute(session, scene, m["id"], granted)
    await _execute(session, scene, m["id"], granted)
    (f,) = findings()                                 # same fingerprint → one finding
    assert f.status == "open"

    await _execute(session, scene, m["id"], timeout)  # inconclusive changes nothing
    assert findings()[0].status == "open"

    await _execute(session, scene, m["id"], denied)
    assert findings()[0].status == "resolved"

    await _execute(session, scene, m["id"], granted)  # regression reopens
    assert findings()[0].status == "open"

    engine = _sync()
    with Session(engine) as s:
        row = s.get(SecurityFinding, f.id)
        row.status = "false_positive"
        s.add(row)
        s.commit()
    engine.dispose()
    await _execute(session, scene, m["id"], granted)
    (f,) = findings()
    assert f.status == "false_positive"


async def test_allow_failure_is_a_regression_not_a_finding(session, scene):
    a, b = await _personas(session, scene)
    await _enable_security(session, scene)
    m = await api.create_matrix(scene.project_id, _body([a, b], [_req_row("orders", a, b)]), REQ,
                                session=session, principal=_me(scene))
    run_id, _ = await _execute(session, scene, m["id"],
                               {("orders", str(a.id)): {"status": 403}, ("orders", str(b.id)): {"status": 403}})
    out = await api.get_matrix_run(run_id, session=session, principal=_me(scene))
    assert out["summary"]["allow_regressions"] == 1
    assert out["summary"]["access_granted"] == 0
    assert (await session.exec(select(SecurityFinding))).all() == []


async def test_screenshot_url_only_for_own_prefix(session, scene):
    a, b = await _personas(session, scene)
    await _enable_security(session, scene)
    m = await api.create_matrix(scene.project_id, _body([a, b], [_req_row("orders", a, b)]), REQ,
                                session=session, principal=_me(scene))
    run_id, _ = await _execute(session, scene, m["id"], {
        ("orders", str(a.id)): {"status": 200, "screenshot": "runs/1/screenshots/x.png"}})
    out = await api.get_matrix_run(run_id, session=session, principal=_me(scene))
    cell = next(c for c in out["cells"] if c["persona_key"] == str(a.id))
    with pytest.raises(HTTPException) as exc:
        await api.cell_screenshot(run_id, cell["id"], session=session, principal=_me(scene))
    assert exc.value.status_code == 404


async def test_deleting_the_project_removes_matrices_runs_and_cells(session, scene):
    from app.services.entity_delete import cascade_delete
    a, b = await _personas(session, scene)
    await _enable_security(session, scene)
    m = await api.create_matrix(scene.project_id, _body([a, b], [_req_row("orders", a, b)]), REQ,
                                session=session, principal=_me(scene))
    await _execute(session, scene, m["id"], {})
    report = await cascade_delete(session, "project", [scene.project_id])
    await session.commit()
    assert (await session.exec(select(AccessMatrixRun))).all() == []
    assert (await session.exec(select(AccessMatrixCell))).all() == []
    assert any(k.startswith("access-matrix/") for k in report.object_keys)


async def test_findings_endpoint_links_back_to_the_granting_run(session, scene):
    a, b = await _personas(session, scene)
    await _enable_security(session, scene)
    m = await api.create_matrix(scene.project_id, _body([a, b], [_req_row("orders", a, b)]), REQ,
                                session=session, principal=_me(scene))
    other = await api.create_matrix(scene.project_id, _body([a, b], [_req_row("users", a, b)]), REQ,
                                    session=session, principal=_me(scene))
    granted = {("orders", str(a.id)): {"status": 200}, ("orders", str(b.id)): {"status": 200},
               ("users", str(a.id)): {"status": 200}, ("users", str(b.id)): {"status": 200}}
    first, _ = await _execute(session, scene, m["id"], granted)
    second, _ = await _execute(session, scene, m["id"], granted)
    await _execute(session, scene, other["id"], granted)
    out = await api.matrix_findings(m["id"], session=session, principal=_me(scene))
    assert len(out) == 1                          # the other matrix's finding is not here
    assert out[0]["matrix_run_id"] == second and out[0]["persona_key"] == str(b.id)
    assert out[0]["row_key"] == "orders" and out[0]["category"] == "access_control"


async def test_retention_sweeps_old_matrix_runs_but_keeps_the_matrix(session, scene):
    from datetime import datetime, timedelta

    from sqlalchemy import text

    from app.models import AccessMatrix
    from app.tasks.cleanup_tasks import purge_old_runs

    a, b = await _personas(session, scene)
    await _enable_security(session, scene)
    m = await api.create_matrix(scene.project_id, _body([a, b], [_req_row("orders", a, b)]), REQ,
                                session=session, principal=_me(scene))
    old_run, _ = await _execute(session, scene, m["id"], {
        ("orders", str(a.id)): {"status": 200}, ("orders", str(b.id)): {"status": 403}})
    new_run, _ = await _execute(session, scene, m["id"], {
        ("orders", str(a.id)): {"status": 200}, ("orders", str(b.id)): {"status": 403}})
    # An in-flight execution is never swept, however old.
    stuck = await api.run_matrix(m["id"], REQ, api.MatrixRunRequest(), session=session,
                                 principal=_me(scene))
    await session.exec(text("UPDATE project SET data_policy = CAST(:p AS JSON) WHERE id = :i"),
                       params={"p": '{"retention_days": 7}', "i": scene.project_id})
    await session.exec(text("UPDATE accessmatrixrun SET created_at = :t WHERE id IN (:a, :s)"),
                       params={"t": datetime.utcnow() - timedelta(days=30),
                               "a": old_run, "s": stuck["id"]})
    await session.commit()

    purge_old_runs()

    engine = _sync()
    with Session(engine) as s:
        left = sorted(r.id for r in s.exec(select(AccessMatrixRun)).all())
        assert left == sorted([new_run, stuck["id"]])
        assert s.exec(select(AccessMatrixCell).where(
            AccessMatrixCell.matrix_run_id == old_run)).all() == []
        assert s.exec(select(AccessMatrixCell).where(
            AccessMatrixCell.matrix_run_id == new_run)).all() != []
        assert s.get(AccessMatrix, m["id"]) is not None
    engine.dispose()
