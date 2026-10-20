"""OpenAPI spec history + the breaking-change gate check (WP16b, V2).

`ProjectApiSpec` (WP11) is the CURRENT document and stays what dispatch
resolves `expect_schema` against. This module appends a numbered, hashed
`ProjectApiSpecVersion` on every store, prunes history, and runs
`openapi_diff` between versions with the project's tests linked in.

Rules:
* An identical document (same canonical-JSON SHA-256 as the newest version)
  does not create a version — re-uploading the same file is not a change.
* The newest MAX_SPEC_VERSIONS are kept; older ones are deleted, except the
  version the gate policy pins as its baseline (deleting that would turn a
  pinned gate into permanent "missing data").
* Version numbers are allocated under a row lock on the project, so two
  concurrent stores cannot both take N+1.
* Deleting the current spec (DELETE .../openapi-spec) keeps the history: the
  versions are the record of what the API contract was.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import select

from app.services import api_specs, openapi_diff

MAX_SPEC_VERSIONS = 50


def sha256_of(spec: Dict[str, Any]) -> str:
    return hashlib.sha256(openapi_diff.canonical_json(spec).encode("utf-8")).hexdigest()


def pinned_version(project: Any) -> Optional[int]:
    policy = getattr(project, "quality_gate_policy", None) or {}
    if isinstance(policy, dict) and policy.get("api_baseline") == "pinned":
        v = policy.get("api_baseline_version")
        return v if isinstance(v, int) and not isinstance(v, bool) else None
    return None


async def latest(session, project_id: int):
    from app.models import ProjectApiSpecVersion as V
    return (await session.exec(select(V).where(V.project_id == project_id)
                               .order_by(V.version.desc()).limit(1))).first()


async def get_version(session, project_id: int, version: int):
    from app.models import ProjectApiSpecVersion as V
    return (await session.exec(select(V).where(V.project_id == project_id, V.version == version))).first()


async def list_versions(session, project_id: int, limit: int = MAX_SPEC_VERSIONS + 1):
    from app.models import ProjectApiSpecVersion as V
    return list((await session.exec(select(V).where(V.project_id == project_id)
                                    .order_by(V.version.desc()).limit(limit))).all())


async def append_version(session, project, spec: Dict[str, Any], *, user_id: Optional[int],
                         source_url: Optional[str], via: str) -> Tuple[Any, bool]:
    """(version row, created?). Caller commits."""
    from app.models import Project, ProjectApiSpecVersion as V
    # Serialise version allocation per project (no-op on SQLite).
    await session.exec(select(Project.id).where(Project.id == project.id).with_for_update())
    digest = sha256_of(spec)
    newest = await latest(session, project.id)
    if newest is not None and newest.sha256 == digest:
        return newest, False
    summary = api_specs.spec_summary(spec)
    row = V(project_id=project.id, version=(newest.version + 1) if newest else 1, sha256=digest,
            spec=spec, title=summary["title"], spec_version=summary["version"], source_url=source_url,
            operation_count=len(summary["operations"]), via=via, created_by_id=user_id,
            created_at=datetime.utcnow())
    session.add(row)
    await session.flush()
    keep_from = row.version - MAX_SPEC_VERSIONS + 1
    if keep_from > 1:
        pinned = pinned_version(project)
        old = (await session.exec(select(V).where(V.project_id == project.id, V.version < keep_from))).all()
        for o in old:
            if o.version != pinned:
                await session.delete(o)
    return row, True


def version_read(row, *, current_sha: Optional[str] = None, include_spec: bool = False) -> Dict[str, Any]:
    out = {"version": row.version, "sha256": row.sha256, "title": row.title,
           "spec_version": row.spec_version, "source_url": row.source_url,
           "operation_count": row.operation_count, "via": row.via,
           "created_by_id": row.created_by_id, "created_at": row.created_at,
           "is_current": current_sha is not None and current_sha == row.sha256}
    if include_spec:
        out["spec"] = row.spec
    return out


async def project_cases_with_refs(session, project_id: int) -> List[Dict[str, Any]]:
    """Cases of this project whose steps reference the spec. Only the
    project's own cases — a diff never names another project's tests."""
    from app.models import TestCase
    rows = (await session.exec(select(TestCase.id, TestCase.name, TestCase.test_suite_id, TestCase.steps)
                               .where(TestCase.project_id == project_id))).all()
    return [{"id": i, "name": n, "test_suite_id": s, "steps": st} for i, n, s, st in rows
            if api_specs.has_openapi_refs(st)]


async def diff_versions(session, project_id: int, old_row, new_row) -> Dict[str, Any]:
    raw = openapi_diff.diff_specs(old_row.spec or {}, new_row.spec or {})
    cases = await project_cases_with_refs(session, project_id)
    linked = openapi_diff.link_cases(raw, old_row.spec or {}, new_row.spec or {}, cases)
    out = openapi_diff.to_json(linked)
    out["from_version"] = old_row.version
    out["to_version"] = new_row.version
    out["from_sha256"] = old_row.sha256
    out["to_sha256"] = new_row.sha256
    return out


async def breaking_change_check(session, project, policy):
    """Gate check `api_breaking_changes` (policy.require_no_breaking_api_changes)."""
    from app.models import QualityGateCheck
    name, threshold = "api_breaking_changes", "0 breaking changes"
    newest = await latest(session, project.id)
    if policy.api_baseline == "pinned":
        if policy.api_baseline_version is None:
            return QualityGateCheck(name=name, passed=False, actual="no baseline", threshold=threshold,
                                    missing_data=True, detail="api_baseline is 'pinned' but no version is set")
        baseline = await get_version(session, project.id, policy.api_baseline_version)
        if baseline is None:
            return QualityGateCheck(name=name, passed=False, actual="no baseline", threshold=threshold,
                                    missing_data=True,
                                    detail=f"pinned spec version {policy.api_baseline_version} does not exist")
    else:
        baseline = None
        if newest is not None:
            from app.models import ProjectApiSpecVersion as V
            baseline = (await session.exec(select(V).where(V.project_id == project.id, V.version < newest.version)
                                           .order_by(V.version.desc()).limit(1))).first()
    if newest is None or baseline is None:
        return QualityGateCheck(name=name, passed=False, actual="no comparison", threshold=threshold,
                                missing_data=True, detail="fewer than two stored OpenAPI spec versions")
    if newest.version == baseline.version:
        return QualityGateCheck(name=name, passed=True, actual=f"0 (v{newest.version} is the baseline)",
                                threshold=threshold)
    diff = await diff_versions(session, project.id, baseline, newest)
    n = diff["breaking_count"]
    detail = None
    if n:
        shown = [f"{c['method']} {c['path']}: {c['message']}" for c in diff["changes"]
                 if c["severity"] == "breaking"][:5]
        detail = "; ".join(shown)
        if diff["affected_tests"]:
            detail += f" — {len(diff['affected_tests'])} test(s) affected"
    elif diff["truncated"]:
        detail = "diff truncated: the spec is too large to compare completely"
    return QualityGateCheck(name=name, passed=n == 0 and not diff["truncated"],
                            actual=f"{n} (v{baseline.version} → v{newest.version})",
                            threshold=threshold, detail=detail,
                            missing_data=n == 0 and diff["truncated"])
