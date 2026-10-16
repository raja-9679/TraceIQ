"""Developer-workflow endpoints (WP11): version, reproduce-locally bundle,
project OpenAPI spec, and two-way git sync of test cases.

Every endpoint authorizes against the PROJECT the object lives in, and a
project-scoped API key is held to its own project (`api_key_allows_project`)
whatever its creator could reach — the CLI and CI use exactly those keys.
"""
from __future__ import annotations

import asyncio
import json
import os
import posixpath
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field as PydField
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.jobs import api_key_allows_project
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import (
    CaseProposal, CaseProposalAction, Persona, Project, ProjectApiSpec,
    ProjectEnvironment, TestCase, TestCaseResult, TestRun, TestSuite,
)
from app.services import api_specs, case_sync, repro_bundle
from app.services.access_service import access_service
from app.services.audit import record as audit_record
from app.services.redaction import (
    redact_headers, redact_message_text, redact_step_evidence, redact_text,
)
from app.services.test_service import test_service

router = APIRouter()

# Bumped on a breaking change to any endpoint the CLI calls; the CLI warns
# when its major differs from the server's.
API_VERSION = "1.0"
FEATURES = ["repro-bundle", "case-sync", "expect-schema", "openapi-spec"]
MAX_SPEC_BYTES = 5 * 1024 * 1024


async def _require_project(principal: AuthPrincipal, project_id: Optional[int],
                           session: AsyncSession, min_role: str = "viewer") -> Project:
    if project_id is None:
        raise HTTPException(status_code=404, detail="Not found")
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if principal.api_key is not None and not api_key_allows_project(principal.api_key, project_id):
        raise HTTPException(status_code=403, detail="API key is not scoped to this project")
    if not await access_service.has_project_access(principal.user.id, project_id, session, min_role=min_role):
        raise HTTPException(status_code=403, detail="Access denied" if min_role == "viewer"
                            else f"{min_role.capitalize()} access required")
    return project


@router.get("/version")
async def server_version(principal: AuthPrincipal = Depends(get_current_principal)):
    """Server + API version for client compatibility checks (`traceiq version`).
    Authenticated: an unauthenticated version banner only helps a scanner."""
    return {"version": os.getenv("TRACEIQ_VERSION") or "dev",
            "api_version": API_VERSION, "features": FEATURES}


# ---------------------------------------------------------------------------
# Reproduce-locally bundle
# ---------------------------------------------------------------------------

async def _case_for_result(run: TestRun, result: TestCaseResult, session: AsyncSession) -> Optional[TestCase]:
    case_id = result.test_case_id or run.test_case_id
    if case_id:
        case = await session.get(TestCase, case_id)
        return case if case and case.project_id == run.project_id else None
    # Pre-stamping results carry only a name; resolve it inside the run's
    # suite tree, and only when it is unambiguous.
    if not run.test_suite_id:
        return None
    name = repro_bundle._ROW_SUFFIX_RE.sub("", result.test_name or "")
    matches = [c for c in await test_service.subtree_cases(run.test_suite_id, session) if c.name == name]
    return matches[0] if len(matches) == 1 else None


def _download(key: str, budget: int) -> bytes:
    from app.core.storage import object_store
    limit = min(repro_bundle.MAX_ARTIFACT_BYTES, budget)
    size = object_store.object_size(key)
    if size is None:
        raise ValueError("not found")
    if size > limit:
        raise ValueError(f"too large ({size} bytes)")
    return object_store.get_object_bytes(key, max_bytes=limit)


async def _artifacts(run: TestRun, result: TestCaseResult, evidence: List[dict]) -> tuple:
    keys: List[str] = []
    for value in [result.trace_url, result.video_url, *(result.screenshots or []),
                  *(e.get("screenshot") for e in evidence if isinstance(e, dict))]:
        key = repro_bundle.run_artifact_key(value, run.id)
        if key and key not in keys:
            keys.append(key)
    files: Dict[str, bytes] = {}
    included, skipped = [], []
    budget = repro_bundle.MAX_TOTAL_ARTIFACT_BYTES
    for i, key in enumerate(keys):
        name = f"artifacts/{i:02d}-{posixpath.basename(key)}"
        try:
            data = await asyncio.to_thread(_download, key, budget)
        except Exception as exc:  # noqa: BLE001 — a missing artifact must not sink the bundle
            skipped.append({"key": key, "reason": str(exc)[:200]})
            continue
        budget -= len(data)
        files[name] = data
        included.append({"key": key, "path": name, "bytes": len(data)})
    return files, included, skipped


@router.get("/runs/{run_id}/results/{result_id}/bundle")
async def download_repro_bundle(
    run_id: int,
    result_id: int,
    include_artifacts: bool = True,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """A zip that replays one result locally. Never contains a secret value:
    `ProjectSecret` is not read at all, and literal credentials found in
    steps/settings/data are swapped for `{{secret.REPRO_*}}` placeholders."""
    run = await session.get(TestRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    project = await _require_project(principal, run.project_id, session)
    result = await session.get(TestCaseResult, result_id)
    if not result or result.test_run_id != run.id:
        raise HTTPException(status_code=404, detail="Result not found in this run")
    case = await _case_for_result(run, result, session)
    if case is None:
        raise HTTPException(status_code=404, detail="The test case behind this result no longer exists")

    placeholders: Dict[str, str] = {}
    steps = repro_bundle.scrub_steps(
        [s.dict() if hasattr(s, "dict") else s for s in (case.steps or [])], placeholders)
    settings = repro_bundle.scrub_settings(
        await test_service.get_effective_settings(case.test_suite_id, session), placeholders)
    row_index, data_row = repro_bundle.data_row_for(case.dataset, result.test_name)
    if data_row is not None:
        data_row = repro_bundle.scrub_mapping(data_row, "data", placeholders)

    env = None
    if run.environment_id:
        env = await session.get(ProjectEnvironment, run.environment_id)
        env = env if env and env.project_id == run.project_id else None
    else:
        env = (await session.exec(select(ProjectEnvironment).where(
            ProjectEnvironment.project_id == run.project_id,
            ProjectEnvironment.is_default == True))).first()  # noqa: E712
    env_vars = repro_bundle.scrub_mapping(dict(env.variables or {}), "env", placeholders) if env else {}

    persona_name = None
    if run.persona_id:
        persona = await session.get(Persona, run.persona_id)
        if persona and persona.workspace_id == project.workspace_id:
            persona_name = persona.name

    payload = result.result_payload if isinstance(result.result_payload, dict) else {}
    evidence = redact_step_evidence(payload.get("step_evidence") or []) or []
    files: Dict[str, bytes] = {}
    included, skipped = [], []
    if include_artifacts:
        files, included, skipped = await _artifacts(run, result, evidence)

    run_meta = {
        "run_id": run.id, "project_id": run.project_id, "suite_id": run.test_suite_id,
        "status": str(getattr(run.status, "value", run.status)), "created_at": run.created_at,
        "browser": run.browser, "device": run.device, "target_url": run.target_url,
        "git_commit": run.git_commit, "git_branch": run.git_branch, "git_pr_url": run.git_pr_url,
        "git_repo": run.git_repo, "triggered_by": str(getattr(run.triggered_by, "value", run.triggered_by)),
        "agent_id": run.agent_id, "persona": persona_name, "anonymous": run.anonymous,
        "environment": {"name": env.name, "base_url": env.base_url, "variables": env_vars} if env else None,
    }
    result_meta = {
        "result_id": result.id, "test_name": result.test_name,
        "status": str(getattr(result.status, "value", result.status)),
        "duration_ms": result.duration_ms, "retry_count": result.retry_count, "is_flaky": result.is_flaky,
        "error_message": redact_message_text(result.error_message),
        "request": {"url": redact_text(result.request_url), "method": result.request_method,
                    "headers": redact_headers(result.request_headers or {})},
        "response": {"status": result.response_status,
                     "headers": redact_headers(result.response_headers or {}),
                     "body": redact_text((result.response_body or "")[:1_000_000]) or None},
        "step_evidence": evidence, "attempts": payload.get("attempts") or [],
        "artifacts": included,
    }
    case_meta = {"id": case.id, "name": case.name, "suite_id": case.test_suite_id,
                 "executor": str(getattr(case.executor, "value", case.executor)), "steps": steps,
                 "data_row_index": row_index}
    secret_keys = repro_bundle.secret_refs(steps, settings, data_row, env_vars)
    root = f"traceiq-run{run.id}-result{result.id}"
    files.update({
        "case.json": repro_bundle.dumps(case_meta),
        "settings.json": repro_bundle.dumps(settings),
        "run.json": repro_bundle.dumps(run_meta),
        "result.json": repro_bundle.dumps(result_meta),
        "repro.spec.ts": repro_bundle.generate_spec(
            case_name=case.name, run_id=run.id, result_id=result.id, steps=steps, settings=settings,
            env_vars=env_vars, base_url=env.base_url if env else None, data_row=data_row,
            browser=run.browser, device=run.device).encode("utf-8"),
        "README.txt": repro_bundle.readme(
            case_name=case.name, run_id=run.id, result_id=result.id, secret_keys=secret_keys,
            placeholders=placeholders, artifacts=included, skipped=skipped,
            env_name=env.name if env else None).encode("utf-8"),
    })
    if data_row is not None:
        files["data_row.json"] = repro_bundle.dumps(data_row)

    spool = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)
    await asyncio.to_thread(repro_bundle.build_zip, {f"{root}/{k}": v for k, v in files.items()}, spool)

    await audit_record(session, entity_type="test_run", entity_id=run.id, action="repro_bundle_export",
                       user_id=principal.user.id, workspace_id=project.workspace_id, principal=principal,
                       changes={"result_id": result.id, "artifacts": len(included)})
    await session.commit()

    def _chunks():
        try:
            while True:
                chunk = spool.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            spool.close()

    return StreamingResponse(_chunks(), media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{root}.zip"',
        "Cache-Control": "no-store",
    })


# ---------------------------------------------------------------------------
# Failure summary for PR comments
# ---------------------------------------------------------------------------

FAILED_STATUSES = ("failed", "error")
MAX_ERROR_CHARS = 500
MAX_FAILURE_LINK_TTL = 3600


def failing_step(evidence: List[dict]) -> Optional[dict]:
    """The first step the worker marked failed, trimmed for a PR comment."""
    for e in evidence or []:
        if isinstance(e, dict) and e.get("status") == "failed":
            return {k: e.get(k) for k in ("index", "type", "selector", "error", "screenshot") if e.get(k) is not None}
    return None


@router.get("/runs/{run_id}/failures")
async def run_failures(
    run_id: int,
    ttl_seconds: int = Query(default=3600, ge=60, le=3600),
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    """Failing results with the failing step, a truncated error and a
    short-lived presigned screenshot link — what the GitHub Action posts.

    Screenshot keys are accepted only under `runs/{run_id}/` (result rows can
    come from local workers), so the link can never point at another run's
    artifacts; it expires after `ttl_seconds` (default one hour)."""
    # These links end up in PR comments, which outlive the run and can be
    # public: an hour at most, also when called without FastAPI's validation.
    ttl_seconds = max(60, min(int(ttl_seconds), MAX_FAILURE_LINK_TTL))
    run = await session.get(TestRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    await _require_project(principal, run.project_id, session)
    results = (await session.exec(select(TestCaseResult).where(
        TestCaseResult.test_run_id == run.id).order_by(TestCaseResult.id))).all()
    from app.core.storage import minio_client
    out = []
    for r in results:
        status = str(getattr(r.status, "value", r.status)).lower()
        if status not in FAILED_STATUSES:
            continue
        payload = r.result_payload if isinstance(r.result_payload, dict) else {}
        step = failing_step(redact_step_evidence(payload.get("step_evidence") or []) or [])
        key = repro_bundle.run_artifact_key((step or {}).pop("screenshot", None), run.id) if step else None
        if key is None:
            for candidate in reversed(r.screenshots or []):
                key = repro_bundle.run_artifact_key(candidate, run.id)
                if key:
                    break
        url = None
        if key:
            try:
                url = await asyncio.to_thread(minio_client.get_presigned_url, key, ttl_seconds)
            except Exception as exc:  # noqa: BLE001 — a link is optional
                print(f"[failures] presign failed for run {run.id}: {exc}")
        error = redact_message_text(r.error_message) or ""
        if step and step.get("error"):
            step["error"] = (redact_message_text(step["error"]) or "")[:MAX_ERROR_CHARS]
        out.append({
            "result_id": r.id, "test_name": r.test_name, "test_case_id": r.test_case_id, "status": status,
            "error": error[:MAX_ERROR_CHARS], "error_truncated": len(error) > MAX_ERROR_CHARS,
            "failing_step": step, "screenshot_url": url,
            "screenshot_expires_in": ttl_seconds if url else None,
        })
    return {"run_id": run.id, "failures": out}


# ---------------------------------------------------------------------------
# Project OpenAPI spec (expect_schema references)
# ---------------------------------------------------------------------------

class ApiSpecUpdate(BaseModel):
    spec: Optional[Dict[str, Any]] = None
    url: Optional[str] = None


async def store_project_spec(session: AsyncSession, project_id: int, spec: Dict[str, Any],
                             user_id: Optional[int], source_url: Optional[str] = None) -> ProjectApiSpec:
    """Upsert the project's spec. Caller validates, audits and commits."""
    if len(json.dumps(spec, default=str)) > MAX_SPEC_BYTES:
        raise HTTPException(status_code=413, detail="OpenAPI spec exceeds 5 MB")
    summary = api_specs.spec_summary(spec)
    row = (await session.exec(select(ProjectApiSpec).where(ProjectApiSpec.project_id == project_id))).first()
    row = row or ProjectApiSpec(project_id=project_id)
    row.spec, row.source_url = spec, source_url
    row.title, row.spec_version = summary["title"], summary["version"]
    row.updated_by_id, row.updated_at = user_id, datetime.utcnow()
    session.add(row)
    return row


def _spec_read(row: ProjectApiSpec, include_spec: bool) -> Dict[str, Any]:
    summary = api_specs.spec_summary(row.spec or {})
    out = {"exists": True, "title": row.title, "version": row.spec_version, "source_url": row.source_url,
           "updated_at": row.updated_at, "operations": summary["operations"]}
    if include_spec:
        out["spec"] = row.spec
    return out


@router.get("/projects/{project_id}/openapi-spec")
async def get_openapi_spec(project_id: int, include_spec: bool = False,
                           principal: AuthPrincipal = Depends(get_current_principal),
                           session: AsyncSession = Depends(get_session)):
    await _require_project(principal, project_id, session)
    row = (await session.exec(select(ProjectApiSpec).where(ProjectApiSpec.project_id == project_id))).first()
    return _spec_read(row, include_spec) if row else {"exists": False, "operations": []}


@router.put("/projects/{project_id}/openapi-spec")
async def put_openapi_spec(project_id: int, body: ApiSpecUpdate,
                           principal: AuthPrincipal = Depends(get_current_principal),
                           session: AsyncSession = Depends(get_session)):
    project = await _require_project(principal, project_id, session, min_role="editor")
    spec = body.spec
    if spec is None and body.url:
        from app.core.net_guard import UnsafeUrlError, safe_get
        try:
            resp = await safe_get(body.url, timeout=30.0)
        except UnsafeUrlError as exc:
            raise HTTPException(status_code=400, detail=f"url refused: {exc}")
        if resp.status_code >= 300:
            raise HTTPException(status_code=502, detail=f"Could not fetch url: {resp.status_code}")
        try:
            spec = resp.json()
        except Exception:  # noqa: BLE001
            import yaml  # type: ignore
            spec = yaml.safe_load(resp.text)
    try:
        api_specs.validate_spec(spec)
    except api_specs.SchemaRefError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    row = await store_project_spec(session, project_id, spec, principal.user.id,
                                   source_url=body.url if body.spec is None else None)
    await audit_record(session, entity_type="project", entity_id=project_id, action="openapi_spec_set",
                       user_id=principal.user.id, workspace_id=project.workspace_id, principal=principal,
                       changes={"title": row.title, "version": row.spec_version, "source_url": row.source_url})
    await session.commit()
    await session.refresh(row)
    return _spec_read(row, False)


@router.delete("/projects/{project_id}/openapi-spec")
async def delete_openapi_spec(project_id: int, principal: AuthPrincipal = Depends(get_current_principal),
                              session: AsyncSession = Depends(get_session)):
    project = await _require_project(principal, project_id, session, min_role="editor")
    row = (await session.exec(select(ProjectApiSpec).where(ProjectApiSpec.project_id == project_id))).first()
    if not row:
        raise HTTPException(status_code=404, detail="No OpenAPI spec stored")
    await session.delete(row)
    await audit_record(session, entity_type="project", entity_id=project_id, action="openapi_spec_delete",
                       user_id=principal.user.id, workspace_id=project.workspace_id, principal=principal)
    await session.commit()
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Two-way git sync
# ---------------------------------------------------------------------------

async def _suite_tree(suite_id: int, principal: AuthPrincipal, session: AsyncSession, min_role: str):
    suite = await session.get(TestSuite, suite_id)
    if not suite:
        raise HTTPException(status_code=404, detail="Suite not found")
    project = await _require_project(principal, suite.project_id, session, min_role=min_role)
    suite_ids = await test_service.subtree_suite_ids(suite_id, session)
    suites = (await session.exec(select(TestSuite).where(TestSuite.id.in_(suite_ids)))).all()
    cases = await test_service.subtree_cases(suite_id, session)
    return suite, project, suites, cases


@router.get("/suites/{suite_id}/sync")
async def export_suite_sync(suite_id: int, principal: AuthPrincipal = Depends(get_current_principal),
                            session: AsyncSession = Depends(get_session)):
    """The suite tree as sync documents (one per case, each with its content
    hash). `traceiq pull` writes them as files."""
    suite, project, suites, cases = await _suite_tree(suite_id, principal, session, "viewer")
    return {
        "format": case_sync.FORMAT_VERSION, "project_id": project.id, "root_suite_id": suite.id,
        "suites": sorted(({"id": s.id, "parent_id": s.parent_id if s.id != suite.id else None, "name": s.name}
                          for s in suites), key=lambda s: s["id"]),
        "cases": sorted((case_sync.case_doc(c) for c in cases), key=lambda d: d["id"]),
    }


class SyncRequest(BaseModel):
    cases: List[Dict[str, Any]] = PydField(default_factory=list)
    # [{id, base_hash}] — cases deleted locally since the last pull.
    deleted: List[Dict[str, Any]] = PydField(default_factory=list)
    dry_run: bool = False
    rationale: Optional[str] = PydField(default=None, max_length=2000)
    # Same meaning as an agent proposal's ai_confidence: only matters when the
    # workspace has an auto-apply threshold.
    confidence: float = PydField(default=0.0, ge=0.0, le=1.0)


def _same_pending(pending: List[CaseProposal], action: CaseProposalAction, target: Optional[int],
                  suite: Optional[int], payload: Dict[str, Any]) -> Optional[CaseProposal]:
    for p in pending:
        if p.action == action and p.target_case_id == target and p.test_suite_id == suite \
                and (p.payload or {}) == payload:
            return p
    return None


@router.post("/suites/{suite_id}/sync")
async def import_suite_sync(suite_id: int, body: SyncRequest,
                            principal: AuthPrincipal = Depends(get_current_principal),
                            session: AsyncSession = Depends(get_session)):
    """Turn an edited file set into CaseProposals.

    Creates/updates/moves/deletes are PROPOSED, never written: the review
    queue and the workspace auto-apply policy decide, exactly as for agent
    writes. A case changed on the server since it was pulled is reported as a
    conflict and nothing is proposed for it."""
    if len(body.cases) + len(body.deleted) > case_sync.MAX_SYNC_CASES:
        raise HTTPException(status_code=413, detail=f"At most {case_sync.MAX_SYNC_CASES} cases per sync")
    # Every planned change is stored as a proposal payload, so the file set as
    # a whole is bounded too, not just the count (per-case limits are in
    # case_sync.validate_doc).
    if len(json.dumps(body.cases, default=str)) > case_sync.MAX_SYNC_BYTES:
        raise HTTPException(status_code=413,
                            detail=f"Sync payload exceeds {case_sync.MAX_SYNC_BYTES // (1024 * 1024)} MB")
    suite, project, suites, cases = await _suite_tree(suite_id, principal, session, "editor")
    in_tree = {s.id for s in suites}
    by_id = {c.id: c for c in cases}
    server_docs = {c.id: case_sync.case_doc(c) for c in cases}

    report: Dict[str, Any] = {"dry_run": body.dry_run, "created": [], "updated": [], "moved": [],
                              "deleted": [], "unchanged": [], "conflicts": [], "errors": [],
                              "already_proposed": [], "proposal_ids": [], "auto_applied": []}
    planned: List[Dict[str, Any]] = []
    seen_ids: set = set()

    for i, doc in enumerate(body.cases):
        err = case_sync.validate_doc(doc)
        label = {"index": i, "id": doc.get("id") if isinstance(doc, dict) else None,
                 "name": doc.get("name") if isinstance(doc, dict) else None}
        if err:
            report["errors"].append({**label, "error": err})
            continue
        target_suite = doc.get("suite_id")
        if target_suite is not None and target_suite not in in_tree:
            report["errors"].append({**label, "error": "suite_id is not in this suite tree"})
            continue
        if doc.get("id") is None:
            payload = {k: v for k, v in case_sync.content_of(doc).items()}
            planned.append({"action": CaseProposalAction.CREATE, "target": None,
                            "suite": target_suite or suite.id, "payload": payload, "bucket": "created",
                            "label": label})
            continue
        cid = doc["id"]
        if cid in seen_ids:
            report["errors"].append({**label, "error": "duplicate id in this sync"})
            continue
        seen_ids.add(cid)
        if cid not in by_id:
            # Either deleted on the server, or a case from somewhere this
            # caller is not syncing (another suite tree or project) — refuse
            # to touch it either way, and don't say which.
            report["conflicts"].append({**label, "reason": "case is not in this suite tree on the server"})
            continue
        verdict = case_sync.classify(server_docs[cid], doc)
        if verdict["kind"] == "unchanged":
            report["unchanged"].append(cid)
        elif verdict["kind"] == "conflict":
            report["conflicts"].append({**label, **{k: v for k, v in verdict.items() if k != "kind"}})
        else:
            if verdict["changes"]:
                planned.append({"action": CaseProposalAction.UPDATE, "target": cid, "suite": None,
                                "payload": {**verdict["changes"], "sync_base_hash": verdict["base_hash"]},
                                "bucket": "updated", "label": label})
            if verdict["move_to"]:
                planned.append({"action": CaseProposalAction.MOVE, "target": cid, "suite": None,
                                "payload": {"new_test_suite_id": verdict["move_to"]},
                                "bucket": "moved", "label": label})

    for i, item in enumerate(body.deleted):
        cid = item.get("id") if isinstance(item, dict) else None
        label = {"index": i, "id": cid, "name": None}
        if not isinstance(cid, int) or isinstance(cid, bool) or cid in seen_ids:
            report["errors"].append({**label, "error": "deleted entries need a unique integer id "
                                                        "that is not also submitted as a case"})
            continue
        seen_ids.add(cid)
        if cid not in by_id:
            report["unchanged"].append(cid)  # already gone: nothing to propose
            continue
        label["name"] = by_id[cid].name
        server_hash = server_docs[cid]["hash"]
        if item.get("base_hash") != server_hash:
            report["conflicts"].append({**label, "reason": "changed on the server since it was pulled",
                                        "server_hash": server_hash, "base_hash": item.get("base_hash")})
            continue
        planned.append({"action": CaseProposalAction.DELETE, "target": cid, "suite": None,
                        "payload": {"reason": "deleted in git sync", "sync_base_hash": server_hash},
                        "bucket": "deleted", "label": label})

    pending = (await session.exec(select(CaseProposal).where(
        CaseProposal.project_id == project.id, CaseProposal.status == "pending"))).all()
    agent = principal.agent_id or "git-sync"
    from app.api.agent_ownership import maybe_auto_apply
    for plan in planned:
        entry = dict(plan["label"])
        dup = _same_pending(pending, plan["action"], plan["target"], plan["suite"], plan["payload"])
        if dup:
            report["already_proposed"].append({**entry, "proposal_id": dup.id})
            continue
        if body.dry_run:
            report[plan["bucket"]].append(entry)
            continue
        proposal = CaseProposal(
            project_id=project.id, test_suite_id=plan["suite"], target_case_id=plan["target"],
            action=plan["action"], payload=plan["payload"],
            rationale=body.rationale or "git sync", ai_confidence=body.confidence, agent_id=agent,
            created_by_id=principal.user.id, created_by_agent_id=agent,
            agent_session_id=principal.agent_session_id,
        )
        session.add(proposal)
        await session.flush()
        entry["proposal_id"] = proposal.id
        report["proposal_ids"].append(proposal.id)
        if await maybe_auto_apply(proposal, principal.user.id, session):
            report["auto_applied"].append(proposal.id)
        report[plan["bucket"]].append(entry)

    if not body.dry_run and report["proposal_ids"]:
        await audit_record(session, entity_type="suite", entity_id=suite.id, action="git_sync_import",
                           user_id=principal.user.id, workspace_id=project.workspace_id, principal=principal,
                           changes={"proposals": report["proposal_ids"],
                                    "conflicts": len(report["conflicts"])})
    await session.commit()
    return report
