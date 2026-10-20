"""Deployment events + DORA metrics (WP12b, decision R3).

Surface:
    POST   /api/projects/{id}/deployments   record one deployment (editor, or a
                                            project-scoped API key whose user is editor)
    GET    /api/projects/{id}/deployments   ?limit=&environment_id=
    DELETE /api/deployments/{id}            (editor)
    GET    /api/projects/{id}/dora          ?days=30&environment_id=

CI is the main caller. `external_id` (the CI run id) makes a retried step
idempotent: a second POST with the same id updates the first record instead of
counting a second deployment. The maths lives in app.services.dora.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field as PydField, field_validator
from sqlmodel import func, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.reliability_reports import require_project
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import Deployment, Incident, ProjectEnvironment, TestRun, TestStatus
from app.services import dora as dora_svc
from app.services.run_scope import counted
from app.services.audit import record as audit_record

router = APIRouter()

MAX_CLOCK_SKEW = timedelta(minutes=10)


def _naive_utc(v: Optional[datetime]) -> Optional[datetime]:
    if v is None or v.tzinfo is None:
        return v
    return v.astimezone(timezone.utc).replace(tzinfo=None)


class DeploymentIn(BaseModel):
    environment_id: Optional[int] = None
    # CI usually knows the environment's name, not its id.
    environment: Optional[str] = PydField(default=None, max_length=100)
    git_commit: Optional[str] = PydField(default=None, max_length=64)
    git_branch: Optional[str] = PydField(default=None, max_length=255)
    git_repo: Optional[str] = PydField(default=None, max_length=255)
    version: Optional[str] = PydField(default=None, max_length=200)
    status: Literal["success", "failed"] = "success"
    deployed_at: Optional[datetime] = None
    commit_at: Optional[datetime] = None
    source: str = PydField(default="api", max_length=40)
    external_id: Optional[str] = PydField(default=None, max_length=200)
    url: Optional[str] = PydField(default=None, max_length=2000)

    @field_validator("url")
    @classmethod
    def _http_url(cls, v: Optional[str]) -> Optional[str]:
        if v and not v.lower().startswith(("http://", "https://")):
            raise ValueError("url must be an http(s) link")
        return v

    @field_validator("deployed_at", "commit_at")
    @classmethod
    def _utc(cls, v: Optional[datetime]) -> Optional[datetime]:
        return _naive_utc(v)


def _out(d: Deployment, env_names: dict) -> dict:
    return {
        "id": d.id, "project_id": d.project_id, "environment_id": d.environment_id,
        "environment": env_names.get(d.environment_id), "git_commit": d.git_commit,
        "git_branch": d.git_branch, "git_repo": d.git_repo, "version": d.version,
        "status": d.status, "deployed_at": d.deployed_at, "commit_at": d.commit_at,
        "source": d.source, "external_id": d.external_id, "url": d.url,
        "created_by_id": d.created_by_id, "api_key_id": d.api_key_id, "created_at": d.created_at,
    }


async def _env_names(project_id: int, session: AsyncSession) -> dict:
    return {e.id: e.name for e in (await session.exec(select(ProjectEnvironment).where(
        ProjectEnvironment.project_id == project_id))).all()}


async def _resolve_env(project_id: int, body: DeploymentIn, session: AsyncSession) -> Optional[int]:
    if body.environment_id is not None:
        env = await session.get(ProjectEnvironment, body.environment_id)
        if not env or env.project_id != project_id:
            raise HTTPException(status_code=404, detail="Environment not found in this project")
        return env.id
    if body.environment:
        env = (await session.exec(select(ProjectEnvironment).where(
            ProjectEnvironment.project_id == project_id,
            ProjectEnvironment.name == body.environment.strip()))).first()
        if not env:
            raise HTTPException(status_code=404, detail="Environment not found in this project")
        return env.id
    return None


@router.post("/projects/{project_id}/deployments", status_code=201)
async def record_deployment(project_id: int, body: DeploymentIn, response: Response,
                            principal: AuthPrincipal = Depends(get_current_principal),
                            session: AsyncSession = Depends(get_session)):
    project = await require_project(project_id, principal, session, min_role="editor")
    env_id = await _resolve_env(project_id, body, session)
    now = datetime.utcnow()
    deployed_at = body.deployed_at or now
    if deployed_at > now + MAX_CLOCK_SKEW:
        raise HTTPException(status_code=422, detail="deployed_at is in the future")
    if body.commit_at and body.commit_at > deployed_at:
        raise HTTPException(status_code=422, detail="commit_at is after deployed_at")

    fields = dict(
        environment_id=env_id, git_commit=(body.git_commit or "").strip() or None,
        git_branch=body.git_branch, git_repo=body.git_repo, version=body.version,
        status=body.status, deployed_at=deployed_at, commit_at=body.commit_at,
        source=body.source or "api", url=body.url,
    )
    dep = None
    if body.external_id:
        dep = (await session.exec(select(Deployment).where(
            Deployment.project_id == project_id,
            Deployment.external_id == body.external_id))).first()
    created = dep is None
    if created:
        dep = Deployment(project_id=project_id, external_id=body.external_id,
                         created_by_id=principal.user.id,
                         api_key_id=principal.api_key.id if principal.api_key else None, **fields)
    else:
        for k, v in fields.items():
            setattr(dep, k, v)
        response.status_code = 200
    session.add(dep)
    await session.flush()
    await audit_record(session, entity_type="deployment", entity_id=dep.id,
                       action="create" if created else "update", principal=principal,
                       workspace_id=project.workspace_id,
                       changes={"git_commit": dep.git_commit, "version": dep.version,
                                "environment_id": env_id, "status": dep.status})
    await session.commit()
    await session.refresh(dep)
    return {**_out(dep, await _env_names(project_id, session)), "created": created}


@router.get("/projects/{project_id}/deployments")
async def list_deployments(project_id: int, limit: int = Query(50, ge=1, le=500),
                           environment_id: Optional[int] = None,
                           principal: AuthPrincipal = Depends(get_current_principal),
                           session: AsyncSession = Depends(get_session)):
    await require_project(project_id, principal, session)
    stmt = select(Deployment).where(Deployment.project_id == project_id)
    if environment_id is not None:
        stmt = stmt.where(Deployment.environment_id == environment_id)
    rows = (await session.exec(stmt.order_by(Deployment.deployed_at.desc()).limit(limit))).all()
    names = await _env_names(project_id, session)
    return [_out(d, names) for d in rows]


@router.delete("/deployments/{deployment_id}", status_code=204)
async def delete_deployment(deployment_id: int,
                            principal: AuthPrincipal = Depends(get_current_principal),
                            session: AsyncSession = Depends(get_session)):
    dep = await session.get(Deployment, deployment_id)
    if not dep:
        raise HTTPException(status_code=404, detail="Deployment not found")
    project = await require_project(dep.project_id, principal, session, min_role="editor")
    await audit_record(session, entity_type="deployment", entity_id=dep.id, action="delete",
                       principal=principal, workspace_id=project.workspace_id,
                       changes={"git_commit": dep.git_commit, "version": dep.version})
    await session.delete(dep)
    await session.commit()
    return Response(status_code=204)


@router.get("/projects/{project_id}/dora")
async def dora_metrics(project_id: int, days: int = Query(30, ge=1, le=180),
                       environment_id: Optional[int] = None,
                       principal: AuthPrincipal = Depends(get_current_principal),
                       session: AsyncSession = Depends(get_session)):
    await require_project(project_id, principal, session)
    names = await _env_names(project_id, session)
    if environment_id is not None and environment_id not in names:
        raise HTTPException(status_code=404, detail="Environment not found in this project")
    end = datetime.utcnow()
    start = end - timedelta(days=days)

    dstmt = select(Deployment).where(Deployment.project_id == project_id,
                                     Deployment.deployed_at >= start, Deployment.deployed_at < end)
    if environment_id is not None:
        dstmt = dstmt.where(Deployment.environment_id == environment_id)
    deps = (await session.exec(dstmt)).all()

    incidents = (await session.exec(select(Incident).where(
        Incident.project_id == project_id, Incident.started_at >= start,
        Incident.started_at < end))).all()

    env_ids = {d.environment_id for d in deps}
    failed_runs = []
    if deps:
        conds = [TestRun.environment_id.in_([e for e in env_ids if e is not None])]
        if None in env_ids:
            conds.append(TestRun.environment_id.is_(None))
        failed_runs = (await session.exec(
            select(TestRun.id, TestRun.created_at, TestRun.environment_id).where(
                TestRun.project_id == project_id, TestRun.created_at >= start,
                TestRun.created_at < end,
                TestRun.status.in_([TestStatus.FAILED, TestStatus.ERROR]), counted(),
                or_(*conds)))).all()

    commits = sorted({d.git_commit for d in deps if d.git_commit and not d.commit_at})
    first_run_at = {}
    if commits:
        first_run_at = {c: t for c, t in (await session.exec(
            select(TestRun.git_commit, func.min(TestRun.created_at)).where(
                TestRun.project_id == project_id, TestRun.git_commit.in_(commits), counted())
            .group_by(TestRun.git_commit))).all()}

    result = dora_svc.compute(
        deployments=[dora_svc.Dep(id=d.id, deployed_at=d.deployed_at, environment_id=d.environment_id,
                                  git_commit=d.git_commit, version=d.version, commit_at=d.commit_at,
                                  status=d.status) for d in deps],
        incidents=[dora_svc.Inc(id=i.id, started_at=i.started_at, resolved_at=i.resolved_at,
                                git_commit=i.git_commit, release=i.release) for i in incidents],
        failed_runs=[dora_svc.FailedRun(id=r[0], created_at=r[1], environment_id=r[2]) for r in failed_runs],
        first_run_at=first_run_at, window_start=start, window_end=end)
    for d in result["deployments"]:
        d["environment"] = names.get(d["environment_id"])
    result["deployments"] = result["deployments"][:50]
    return {
        "project_id": project_id, "days": days, "window_start": start, "window_end": end,
        "environment_id": environment_id,
        "environment": names.get(environment_id) if environment_id is not None else None,
        "inputs": {
            "deployments": len(deps), "incidents": len(incidents), "failed_runs": len(failed_runs),
            "commits_with_test_runs": len(first_run_at),
            "notes": [
                "Incidents carry no environment, so they are matched to deployments project-wide.",
                "A failed run counts against the latest deployment on the same environment within 24 h before it.",
            ],
        },
        **result,
    }
