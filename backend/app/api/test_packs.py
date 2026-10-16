"""Integration test packs (WP13c, decision C7).

GET  /api/test-packs                                   gallery (any signed-in user:
                                                       repo content, no tenant data)
GET  /api/test-packs/{pack_id}                         one pack, steps included
POST /api/projects/{id}/test-packs/{pack_id}/import    editor on the project

Import writes rows only — a suite with the pack's cases, non-secret variables
into a project environment ({{env.X}}), secret ones into ProjectSecret
({{secret.X}}, encrypted) — and never contacts the target. A required variable
left empty is reported as `missing` rather than refused, so a user can import
first and fill the environment / secrets later.
"""
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_session
from app.core.secrets import encrypt_secret
from app.models import Project, ProjectEnvironment, ProjectSecret, User
from app.services import test_packs as tp
from app.services.access_service import access_service
from app.services.audit import record as audit_record

router = APIRouter()


class PackImportRequest(BaseModel):
    variables: Dict[str, Optional[str]] = Field(default_factory=dict)
    # Which environment receives the non-secret values; default = the
    # project's default environment (created as "default" if it has none).
    environment_id: Optional[int] = None


@router.get("/test-packs")
async def list_test_packs(current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    packs = [tp.pack_summary(p) for p in tp.list_packs()]
    return {"packs": packs, "total": len(packs), "format_version": tp.PACK_FORMAT_VERSION}


@router.get("/test-packs/{pack_id}")
async def get_test_pack(pack_id: str, current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    pack = tp.get_pack(pack_id)
    if not pack:
        raise HTTPException(status_code=404, detail="Test pack not found")
    return {**tp.pack_summary(pack), "pack": pack}


async def _target_environment(session: AsyncSession, project_id: int,
                              environment_id: Optional[int]) -> ProjectEnvironment:
    if environment_id is not None:
        env = await session.get(ProjectEnvironment, environment_id)
        if not env or env.project_id != project_id:
            raise HTTPException(status_code=404, detail="Environment not found in this project")
        return env
    envs = (await session.exec(select(ProjectEnvironment).where(
        ProjectEnvironment.project_id == project_id).order_by(ProjectEnvironment.id))).all()
    for env in envs:
        if env.is_default:
            return env
    if envs:
        return envs[0]
    env = ProjectEnvironment(project_id=project_id, name="default", is_default=True, variables={})
    session.add(env)
    await session.flush()
    return env


@router.post("/projects/{project_id}/test-packs/{pack_id}/import", status_code=201)
async def import_test_pack(project_id: int, pack_id: str, body: PackImportRequest, request: Request,
                           current_user: User = Depends(get_current_user),
                           session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    if not await access_service.has_project_access(current_user.id, project_id, session, min_role="editor"):
        raise HTTPException(status_code=403, detail="Editor access required")
    pack = tp.get_pack(pack_id)
    if not pack:
        raise HTTPException(status_code=404, detail="Test pack not found")
    try:
        split = tp.split_values(pack, body.variables)
    except tp.PackError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    env = await _target_environment(session, project_id, body.environment_id)
    from app.api.endpoints.test_suites import create_suite_from_data
    suite = await create_suite_from_data(tp.suite_import_data(pack), None, project_id, session,
                                         current_user.id)   # 409 on a name clash
    variables = dict(env.variables or {})
    env_set, env_kept = [], []
    for name, value in split["env"].items():
        # A default never overwrites a value the environment already has.
        if name in split["defaults_used"] and name in variables:
            env_kept.append(name)
            continue
        variables[name] = value
        env_set.append(name)
    env.variables = variables
    env.updated_at = datetime.utcnow()
    session.add(env)

    existing = {s.key: s for s in (await session.exec(select(ProjectSecret).where(
        ProjectSecret.project_id == project_id))).all()}
    secrets_stored = []
    for name, value in split["secret"].items():
        row = existing.get(name)
        if row:
            row.value_encrypted = encrypt_secret(value)
            row.updated_at = datetime.utcnow()
        else:
            row = ProjectSecret(project_id=project_id, key=name, value_encrypted=encrypt_secret(value))
        session.add(row)
        secrets_stored.append(name)
    secret_names = [v["name"] for v in pack.get("variables") or [] if v.get("secret")]
    already_set = [n for n in secret_names if n not in secrets_stored and n in existing]
    missing = [n for n in split["missing"] if n not in already_set and n not in variables]

    await session.flush()
    project = await session.get(Project, project_id)
    await audit_record(session, entity_type="suite", entity_id=suite.id, action="test_pack_import",
                       user_id=current_user.id, request=request,
                       workspace_id=project.workspace_id if project else None,
                       changes={"pack": pack["id"], "version": pack["version"], "project_id": project_id,
                                "environment_id": env.id, "variables_set": sorted(env_set),
                                # names only: a secret's value never reaches the audit trail
                                "secrets_stored": sorted(secrets_stored)})
    await session.commit()
    case_count = sum(len(s.get("cases") or []) for s in pack.get("suites") or [])
    return {
        "suite_id": suite.id, "suite_name": suite.name, "case_count": case_count,
        "pack": {"id": pack["id"], "version": pack["version"]},
        "environment": {"id": env.id, "name": env.name},
        "variables": {"set": sorted(env_set), "defaults_used": sorted(set(split["defaults_used"]) & set(env_set)),
                      "kept_existing": sorted(env_kept)},
        "secrets": {"stored": sorted(secrets_stored), "already_set": sorted(already_set)},
        "missing": sorted(missing),
    }
