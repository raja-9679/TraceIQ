"""WP11 developer workflow against a real Postgres: reproduce-locally bundle,
failure summary, and two-way git sync.

    ./run-tests-live.sh tests/integration/test_developer_workflow_db.py -q
"""
from __future__ import annotations

import io
import json
import os
import zipfile

import pytest
from fastapi import HTTPException
from sqlmodel import select

pytestmark = pytest.mark.skipif(
    not os.getenv("TRACEIQ_LIVE_DB"),
    reason="needs a live Postgres; set TRACEIQ_LIVE_DB=1 and DATABASE_URL")

from app.api import agent_ownership, developer                      # noqa: E402
from app.core.auth import AuthPrincipal                              # noqa: E402
from app.core.secrets import encrypt_secret                         # noqa: E402
from app.models import (                                             # noqa: E402
    ApiKey, AuditLog, CaseProposal, Persona, ProjectApiSpec, ProjectEnvironment, ProjectSecret,
    TestCase, TestCaseResult, TestRun, TestStatus, TestSuite, User,
)

from tests.integration.test_targeted_runs_db import (               # noqa: E402,F401
    no_queue, scene, session,
)

SECRET_VALUE = "sk_live_DO_NOT_LEAK_7731"
LITERAL_BEARER = "Bearer literal-token-in-settings"
LITERAL_PASSWORD = "hunter2-literal"


def _me(scene):
    return AuthPrincipal(user=scene.user, api_key=None)


async def _failing_result(session, scene, *, screenshots=None, evidence=None):
    """A failed run of the first root case, with a stored secret, a persona,
    a literal credential in the suite settings and a password fill step."""
    case = await session.get(TestCase, scene.root_cases[0])
    case.steps = [
        {"id": "s1", "type": "goto", "value": "/login"},
        {"id": "s2", "type": "fill", "selector": "input[type=password]", "value": LITERAL_PASSWORD},
        {"id": "s3", "type": "http-request", "value": "https://api.example/me",
         "params": {"headers": {"X-Token": "{{secret.API_TOKEN}}"}}},
    ]
    case.dataset = [{"user": "a"}, {"user": "b"}]
    suite = await session.get(TestSuite, scene.root_id)
    suite.settings = {"headers": {"Authorization": LITERAL_BEARER, "X-Suite": "root"}}
    session.add_all([case, suite])
    persona = (await session.exec(select(Persona).where(Persona.name == "Admin persona"))).first()
    if persona is None:  # first call in this test
        session.add(ProjectSecret(project_id=scene.project_id, key="API_TOKEN",
                                  value_encrypted=encrypt_secret(SECRET_VALUE)))
        persona = Persona(workspace_id=(await _ws(session, scene)), project_id=scene.project_id,
                          name="Admin persona", session_state={"cookies": [{"value": "persona-cookie"}]},
                          auth_headers={"Authorization": "Bearer persona"})
        env = ProjectEnvironment(project_id=scene.project_id, name="staging", base_url="https://stg.example",
                                 variables={"REGION": "eu"}, is_default=True)
        session.add_all([persona, env])
        await session.flush()
    run = TestRun(test_suite_id=scene.root_id, project_id=scene.project_id, status=TestStatus.FAILED,
                  browser="chromium", git_commit="abc123", persona_id=persona.id)
    session.add(run)
    await session.flush()
    result = TestCaseResult(
        test_run_id=run.id, test_name=f"{case.name} [row 2]", status=TestStatus.FAILED, duration_ms=10,
        error_message="Timeout waiting for #pay", test_case_id=case.id,
        trace_url=f"runs/{run.id}/trace.zip",
        screenshots=screenshots if screenshots is not None else [f"runs/{run.id}/screenshots/end.png"],
        result_payload={"step_evidence": evidence if evidence is not None else [
            {"id": "s0", "index": 0, "type": "goto", "status": "passed"},
            {"id": "s1", "index": 1, "type": "fill", "status": "failed", "selector": "input[type=password]",
             "error": "Timeout 30000ms", "screenshot": f"runs/{run.id}/screenshots/s1.png"}]})
    session.add(result)
    await session.commit()
    return run, result


async def _ws(session, scene):
    from app.models import Project
    return (await session.get(Project, scene.project_id)).workspace_id


async def _bundle_files(resp) -> dict:
    data = b"".join([c async for c in resp.body_iterator])
    zf = zipfile.ZipFile(io.BytesIO(data))
    return {n.split("/", 1)[1]: zf.read(n) for n in zf.namelist()}


@pytest.fixture
def artifacts(monkeypatch):
    fetched = []

    def fake_download(key, budget):
        fetched.append(key)
        return f"bytes of {key}".encode()
    monkeypatch.setattr(developer, "_download", fake_download)
    return fetched


@pytest.fixture
def no_decrypt(monkeypatch):
    """The bundle path must never decrypt a project secret."""
    def boom(*a, **kw):
        raise AssertionError("bundle code decrypted a secret")
    import app.core.secrets as secrets
    monkeypatch.setattr(secrets, "decrypt_secret", boom)
    monkeypatch.setattr(secrets, "decrypt_json", boom)


async def test_bundle_has_everything_and_never_a_secret(session, scene, artifacts, no_decrypt):
    run, result = await _failing_result(session, scene)
    resp = await developer.download_repro_bundle(run.id, result.id, principal=_me(scene), session=session)
    files = await _bundle_files(resp)

    assert {"case.json", "settings.json", "run.json", "result.json", "repro.spec.ts", "README.txt",
            "data_row.json"} <= set(files)
    everything = b"\n".join(files.values()).decode("utf-8", "replace")
    for leaked in (SECRET_VALUE, LITERAL_BEARER, LITERAL_PASSWORD, "persona-cookie", "Bearer persona"):
        assert leaked not in everything

    settings = json.loads(files["settings.json"])
    assert settings["headers"]["Authorization"] == "{{secret.REPRO_HEADER_AUTHORIZATION}}"
    assert settings["headers"]["X-Suite"] == "root"
    steps = json.loads(files["case.json"])["steps"]
    assert steps[1]["value"].startswith("{{secret.REPRO_STEP_2")
    assert steps[2]["params"]["headers"]["X-Token"] == "{{secret.API_TOKEN}}"
    assert json.loads(files["data_row.json"]) == {"user": "b"}            # "[row 2]"
    meta = json.loads(files["run.json"])
    assert meta["persona"] == "Admin persona" and meta["git_commit"] == "abc123"
    assert meta["environment"]["variables"] == {"REGION": "eu"}
    readme = files["README.txt"].decode()
    assert "export TRACEIQ_SECRET_API_TOKEN=" in readme
    assert "export TRACEIQ_SECRET_REPRO_HEADER_AUTHORIZATION=" in readme
    spec = files["repro.spec.ts"].decode()
    assert "@playwright/test" in spec and 'BASE_URL: string = "https://stg.example"' in spec
    evidence = json.loads(files["result.json"])["step_evidence"]
    assert evidence[1]["status"] == "failed"
    assert sorted(artifacts) == sorted([f"runs/{run.id}/trace.zip", f"runs/{run.id}/screenshots/end.png",
                                        f"runs/{run.id}/screenshots/s1.png"])
    assert any(k.startswith("artifacts/") and k.endswith("trace.zip") for k in files)

    audit = (await session.exec(select(AuditLog).where(AuditLog.action == "repro_bundle_export"))).all()
    assert len(audit) == 1 and audit[0].entity_id == run.id


async def test_bundle_only_fetches_this_runs_artifacts(session, scene, artifacts):
    run, result = await _failing_result(session, scene, screenshots=["runs/999999/screenshots/x.png",
                                                                       "https://elsewhere/x.png"], evidence=[])
    resp = await developer.download_repro_bundle(run.id, result.id, principal=_me(scene), session=session)
    await _bundle_files(resp)
    assert artifacts == [f"runs/{run.id}/trace.zip"]


async def test_bundle_cross_project_access_is_refused(session, scene, artifacts):
    run, result = await _failing_result(session, scene)
    outsider = User(email="out@corp.example.com", full_name="Out", hashed_password="x", is_verified=True)
    session.add(outsider)
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await developer.download_repro_bundle(run.id, result.id, principal=AuthPrincipal(user=outsider),
                                              session=session)
    assert exc.value.status_code == 403

    key = ApiKey(name="ci", workspace_id=1, project_id=scene.other_project_id, prefix="tiq_x", hashed_key="h")
    with pytest.raises(HTTPException) as exc:
        await developer.download_repro_bundle(run.id, result.id, principal=AuthPrincipal(user=scene.user, api_key=key),
                                              session=session)
    assert exc.value.status_code == 403

    # A result id from another run is not reachable through this run.
    other_run = TestRun(test_suite_id=scene.root_id, project_id=scene.project_id, status=TestStatus.PASSED)
    session.add(other_run)
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await developer.download_repro_bundle(other_run.id, result.id, principal=_me(scene), session=session)
    assert exc.value.status_code == 404
    assert artifacts == []


async def test_failures_summary_signs_only_this_runs_screenshot(session, scene, monkeypatch):
    from app.core.storage import minio_client
    signed = []
    monkeypatch.setattr(minio_client, "get_presigned_url",
                        lambda key, expiration=3600: signed.append((key, expiration)) or f"https://s/{key}?e={expiration}")
    run, result = await _failing_result(session, scene, screenshots=["runs/424242/screenshots/x.png"])
    out = await developer.run_failures(run.id, ttl_seconds=600, principal=_me(scene), session=session)
    [f] = out["failures"]
    assert f["failing_step"] == {"index": 1, "type": "fill", "selector": "input[type=password]",
                                 "error": "Timeout 30000ms"}
    assert signed == [(f"runs/{run.id}/screenshots/s1.png", 600)]
    assert f["screenshot_expires_in"] == 600

    # Evidence screenshot from another run's prefix and only a foreign result
    # screenshot: nothing is signed.
    signed.clear()
    run2, _ = await _failing_result(session, scene, screenshots=["runs/424242/screenshots/x.png"], evidence=[
        {"id": "s0", "index": 0, "type": "click", "status": "failed", "screenshot": "runs/424242/screenshots/y.png"}])
    out = await developer.run_failures(run2.id, ttl_seconds=600, principal=_me(scene), session=session)
    assert signed == [] and out["failures"][0]["screenshot_url"] is None


# ---------------------------------------------------------------------------
# git sync
# ---------------------------------------------------------------------------

def _local(doc, **changes):
    out = {k: doc[k] for k in ("id", "suite_id", "name", "steps", "tags", "priority", "code_paths")}
    out.update(base_hash=doc["hash"], base_suite_id=doc["suite_id"], **changes)
    return out


async def _proposals(session, scene):
    return (await session.exec(select(CaseProposal).where(CaseProposal.project_id == scene.project_id)
                                .order_by(CaseProposal.id))).all()


async def test_sync_creates_proposals_not_cases(session, scene):
    export = await developer.export_suite_sync(scene.root_id, principal=_me(scene), session=session)
    docs = {d["id"]: d for d in export["cases"]}
    assert set(docs) == set(scene.root_cases + scene.child_cases)
    assert {s["id"] for s in export["suites"]} == {scene.root_id, scene.child_id}
    edited_id, moved_id, deleted_id = scene.root_cases[0], scene.root_cases[1], scene.child_cases[1]
    before = (await session.get(TestCase, edited_id)).steps

    body = developer.SyncRequest(cases=[
        _local(docs[edited_id], steps=[{"id": "n1", "type": "goto", "value": "/new"}]),
        _local(docs[moved_id], suite_id=scene.child_id),
        _local(docs[scene.child_cases[0]]),
        {"name": "brand new", "steps": [], "suite_id": scene.child_id},
    ], deleted=[{"id": deleted_id, "base_hash": docs[deleted_id]["hash"]}], rationale="PR #1")
    report = await developer.import_suite_sync(scene.root_id, body, principal=_me(scene), session=session)

    assert [e["id"] for e in report["updated"]] == [edited_id]
    assert [e["id"] for e in report["moved"]] == [moved_id]
    assert [e["id"] for e in report["deleted"]] == [deleted_id]
    assert [e["name"] for e in report["created"]] == ["brand new"]
    assert report["unchanged"] == [scene.child_cases[0]] and report["conflicts"] == []

    props = await _proposals(session, scene)
    assert sorted(p.action.value for p in props) == ["create", "delete", "move", "update"]
    assert all(p.status == "pending" and p.created_by_id == scene.user.id for p in props)
    edited = await session.get(TestCase, edited_id)
    await session.refresh(edited)
    assert edited.steps == before                                         # nothing written
    assert (await session.get(TestCase, deleted_id)) is not None
    names = (await session.exec(select(TestCase.name).where(TestCase.project_id == scene.project_id))).all()
    assert "brand new" not in names

    # Pushing the same thing again does not stack duplicates.
    again = await developer.import_suite_sync(scene.root_id, body, principal=_me(scene), session=session)
    assert len(again["already_proposed"]) == 4 and again["proposal_ids"] == []
    assert len(await _proposals(session, scene)) == 4


async def test_sync_reports_conflicts_instead_of_overwriting(session, scene):
    export = await developer.export_suite_sync(scene.root_id, principal=_me(scene), session=session)
    doc = next(d for d in export["cases"] if d["id"] == scene.root_cases[0])
    case = await session.get(TestCase, scene.root_cases[0])
    case.name = "renamed in the UI"
    session.add(case)
    await session.commit()

    body = developer.SyncRequest(cases=[_local(doc, steps=[{"id": "x", "type": "goto", "value": "/"}])],
                                 deleted=[])
    report = await developer.import_suite_sync(scene.root_id, body, principal=_me(scene), session=session)
    assert [c["id"] for c in report["conflicts"]] == [scene.root_cases[0]]
    assert "changed on the server" in report["conflicts"][0]["reason"]
    assert await _proposals(session, scene) == []

    stale_delete = developer.SyncRequest(deleted=[{"id": scene.root_cases[0], "base_hash": doc["hash"]}])
    report = await developer.import_suite_sync(scene.root_id, stale_delete, principal=_me(scene), session=session)
    assert report["conflicts"] and await _proposals(session, scene) == []


async def test_sync_refuses_cases_outside_the_tree_and_dry_run_writes_nothing(session, scene):
    body = developer.SyncRequest(cases=[
        {"id": scene.foreign_case, "name": "steal", "steps": [], "base_hash": "sha256:x"},
        {"id": scene.sibling_case, "name": "sibling", "steps": [], "base_hash": "sha256:x"},
        {"name": "misplaced", "steps": [], "suite_id": scene.sibling_id},
        {"name": "ok", "steps": []},
    ], dry_run=True)
    report = await developer.import_suite_sync(scene.root_id, body, principal=_me(scene), session=session)
    assert {c["id"] for c in report["conflicts"]} == {scene.foreign_case, scene.sibling_case}
    assert report["errors"][0]["error"] == "suite_id is not in this suite tree"
    assert [c["name"] for c in report["created"]] == ["ok"] and report["proposal_ids"] == []
    assert await _proposals(session, scene) == []


async def test_sync_access_control(session, scene):
    key = ApiKey(name="ci", workspace_id=1, project_id=scene.other_project_id, prefix="tiq_x", hashed_key="h")
    scoped = AuthPrincipal(user=scene.user, api_key=key)
    for call in (developer.export_suite_sync(scene.root_id, principal=scoped, session=session),
                 developer.import_suite_sync(scene.root_id, developer.SyncRequest(), principal=scoped,
                                             session=session)):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 403


async def test_accepting_a_stale_sync_proposal_is_refused(session, scene):
    export = await developer.export_suite_sync(scene.root_id, principal=_me(scene), session=session)
    doc = next(d for d in export["cases"] if d["id"] == scene.root_cases[0])
    await developer.import_suite_sync(scene.root_id, developer.SyncRequest(cases=[_local(doc, name="from git")]),
                                      principal=_me(scene), session=session)
    [proposal] = await _proposals(session, scene)
    assert proposal.payload["sync_base_hash"] == doc["hash"]

    case = await session.get(TestCase, scene.root_cases[0])
    case.tags = ["edited-meanwhile"]
    session.add(case)
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await agent_ownership._apply_proposal(proposal, scene.user.id, session)
    assert exc.value.status_code == 409


async def test_openapi_spec_round_trip_and_editor_gate(session, scene):
    spec = {"openapi": "3.0.0", "info": {"title": "Shop", "version": "2"},
            "paths": {"/a": {"get": {"operationId": "getA", "responses": {"200": {"description": "ok"}}}}}}
    out = await developer.put_openapi_spec(scene.project_id, developer.ApiSpecUpdate(spec=spec),
                                           principal=_me(scene), session=session)
    assert out["title"] == "Shop" and out["operations"][0]["operationId"] == "getA"
    got = await developer.get_openapi_spec(scene.project_id, include_spec=True, principal=_me(scene),
                                           session=session)
    assert got["spec"] == spec
    rows = (await session.exec(select(ProjectApiSpec))).all()
    assert len(rows) == 1
    with pytest.raises(HTTPException) as exc:
        await developer.put_openapi_spec(scene.project_id, developer.ApiSpecUpdate(spec={"paths": {}}),
                                         principal=_me(scene), session=session)
    assert exc.value.status_code == 400
    outsider = User(email="o2@corp.example.com", full_name="O", hashed_password="x", is_verified=True)
    session.add(outsider)
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await developer.get_openapi_spec(scene.project_id, principal=AuthPrincipal(user=outsider), session=session)
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# security review fixes
# ---------------------------------------------------------------------------

async def test_from_openapi_spec_storage_is_audited(session, scene):
    from app.api import case_generation
    from app.models import CaseFromOpenAPIRequest
    spec = {"openapi": "3.0.0", "info": {"title": "Audited", "version": "1"},
            "servers": [{"url": "https://api.example"}],
            "paths": {"/a": {"get": {"operationId": "getA", "responses": {"200": {"description": "ok"}}}}}}
    out = await case_generation.cases_from_openapi(
        CaseFromOpenAPIRequest(schema_inline=spec, test_suite_id=scene.root_id),
        principal=_me(scene), session=session)
    assert out["created"] == 1
    rows = (await session.exec(select(AuditLog).where(AuditLog.action == "openapi_spec_set"))).all()
    assert rows and rows[-1].entity_id == scene.project_id
    assert rows[-1].changes.get("via") == "cases/from-openapi"
    assert (await session.exec(select(AuditLog).where(AuditLog.action == "cases_from_openapi"))).first()


async def test_failure_links_are_capped_at_an_hour(session, scene, monkeypatch):
    from app.core.storage import minio_client
    signed = []
    monkeypatch.setattr(minio_client, "get_presigned_url",
                        lambda key, expiration=3600: signed.append(expiration) or "https://s/x")
    run, _ = await _failing_result(session, scene)
    out = await developer.run_failures(run.id, ttl_seconds=86400, principal=_me(scene), session=session)
    assert signed == [3600] and out["failures"][0]["screenshot_expires_in"] == 3600


async def test_artifact_url_honours_project_scoped_api_keys(session, scene, monkeypatch):
    from app.api.endpoints import test_runs
    from app.core.storage import minio_client
    monkeypatch.setattr(minio_client, "get_presigned_url", lambda key, expiration=3600: f"https://s/{key}")
    run, _ = await _failing_result(session, scene)
    ws = await _ws(session, scene)
    own = ApiKey(name="own", workspace_id=ws, project_id=scene.project_id, prefix="tiq_o", hashed_key="h1")
    other = ApiKey(name="other", workspace_id=ws, project_id=scene.other_project_id, prefix="tiq_p",
                   hashed_key="h2")
    key = f"runs/{run.id}/trace.zip"
    out = await test_runs.get_artifact_url(key, session=session,
                                           principal=AuthPrincipal(user=scene.user, api_key=own))
    assert out["url"].endswith(key)
    with pytest.raises(HTTPException) as exc:
        await test_runs.get_artifact_url(key, session=session,
                                         principal=AuthPrincipal(user=scene.user, api_key=other))
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        await test_runs.get_artifact_url(f"runs/{run.id}/../{run.id + 1}/trace.zip", session=session,
                                         principal=_me(scene))
    assert exc.value.status_code == 400


async def test_sync_payload_and_document_size_limits(session, scene, monkeypatch):
    from app.services import case_sync
    big = {"name": "x", "steps": [{"type": "goto", "value": "y" * 2000}] * 600}
    assert "steps" in (case_sync.validate_doc(dict(big, steps=big["steps"] * 2)) or "")
    assert "KB" in (case_sync.validate_doc(big) or "")
    assert "name" in (case_sync.validate_doc({"name": "n" * 600, "steps": []}) or "")
    monkeypatch.setattr(case_sync, "MAX_SYNC_BYTES", 1000)
    with pytest.raises(HTTPException) as exc:
        await developer.import_suite_sync(scene.root_id, developer.SyncRequest(
            cases=[{"name": "n", "steps": [{"type": "goto", "value": "z" * 2000}]}]),
            principal=_me(scene), session=session)
    assert exc.value.status_code == 413
