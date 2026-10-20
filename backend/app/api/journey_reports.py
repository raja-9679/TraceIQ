"""Reporting endpoints (WP12c): journey coverage, business journeys, flags.

- analytics imports:  GET/POST /projects/{id}/journey-imports, DELETE /journey-imports/{id}
- path rules:         GET/PUT  /projects/{id}/journey-rules
- coverage map:       GET      /projects/{id}/journey-coverage[?import_id=]
- business journeys:  GET/POST /projects/{id}/business-journeys (GET = the board),
                      PATCH/DELETE /business-journeys/{id}
- flag catalogue:     GET/POST /projects/{id}/flag-catalog, PATCH/DELETE /flag-catalog/{id}
- flag coverage:      GET      /projects/{id}/flag-coverage?days=

Every endpoint resolves the project first and checks access on it (editor for
writes); project-scoped API keys are held to their project. Child rows are
always re-read with their project id, so a guessed id from another project
404s rather than leaking.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field as PydField
from sqlalchemy import String, cast, func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.jobs import api_key_allows_project
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.models import (
    BusinessJourney, FlagCatalogEntry, FlakeRecord, JourneyConfig, JourneyImport, Project,
    Requirement, RequirementLink, TestCase, TestCaseResult, TestRun, User,
)
from app.services import business_journeys as bj
from app.services import flag_coverage as fc
from app.services import journey_coverage as jc
from app.services.access_service import access_service
from app.services.audit import record as audit_record
from app.services.run_scope import counted

router = APIRouter()


async def project_for(session: AsyncSession, principal: AuthPrincipal, project_id: Optional[int],
                      min_role: str = "viewer") -> Project:
    project = await session.get(Project, project_id) if project_id else None
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if principal.api_key is not None and not api_key_allows_project(principal.api_key, project_id):
        raise HTTPException(status_code=403, detail="API key is not scoped to this project")
    if not await access_service.has_project_access(principal.user.id, project_id, session,
                                                   min_role=min_role):
        raise HTTPException(status_code=403, detail="Access denied" if min_role == "viewer"
                            else f"{min_role.capitalize()} role required")
    return project


# ---------------------------------------------------------------------------
# Analytics imports + path rules
# ---------------------------------------------------------------------------

def _import_out(i: JourneyImport) -> Dict[str, Any]:
    return {"id": i.id, "label": i.label, "filename": i.filename,
            "source_format": i.source_format, "row_count": i.row_count,
            "skipped_rows": i.skipped_rows, "path_count": i.path_count,
            "total_views": i.total_views, "period_start": i.period_start,
            "period_end": i.period_end, "warnings": i.warnings or [],
            "journeys": sorted({r["journey"] for r in (i.rows or []) if r.get("journey")}),
            "created_by_id": i.created_by_id,
            "created_at": i.created_at.isoformat() if i.created_at else None}


@router.get("/projects/{project_id}/journey-imports")
async def list_imports(project_id: int, session: AsyncSession = Depends(get_session),
                       principal: AuthPrincipal = Depends(get_current_principal)):
    await project_for(session, principal, project_id)
    rows = (await session.exec(select(JourneyImport).where(
        JourneyImport.project_id == project_id).order_by(JourneyImport.id.desc()))).all()
    return {"imports": [_import_out(i) for i in rows],
            "limits": {"max_bytes": jc.MAX_IMPORT_BYTES, "max_rows": jc.MAX_IMPORT_ROWS,
                       "kept": jc.MAX_IMPORTS_KEPT}}


@router.post("/projects/{project_id}/journey-imports")
async def create_import(project_id: int, request: Request,
                        file: UploadFile = File(...),
                        label: Optional[str] = Form(None),
                        format: Optional[str] = Form(None),
                        session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    project = await project_for(session, principal, project_id, "editor")
    # Bounded read: never pull more than the cap (+1 to detect overflow).
    data = await file.read(jc.MAX_IMPORT_BYTES + 1)
    try:
        parsed = jc.parse_import(data, format, file.filename)
    except jc.JourneyImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    imp = JourneyImport(
        project_id=project_id, label=(label or "").strip()[:200] or None,
        filename=(file.filename or "")[:255] or None, source_format=parsed.source_format,
        row_count=parsed.row_count, skipped_rows=parsed.skipped_rows,
        path_count=len(parsed.rows), total_views=parsed.total_views,
        period_start=parsed.period_start, period_end=parsed.period_end,
        rows=parsed.rows, warnings=parsed.warnings, created_by_id=principal.user.id)
    session.add(imp)
    await session.flush()
    # Keep the newest MAX_IMPORTS_KEPT versions.
    old = (await session.exec(select(JourneyImport).where(
        JourneyImport.project_id == project_id).order_by(JourneyImport.id.desc())
        .offset(jc.MAX_IMPORTS_KEPT))).all()
    for o in old:
        await session.delete(o)
    await audit_record(session, entity_type="journey_import", entity_id=imp.id, action="create",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"filename": imp.filename, "rows": imp.row_count,
                                "total_views": imp.total_views})
    await session.commit()
    await session.refresh(imp)
    return _import_out(imp)


@router.delete("/journey-imports/{import_id}")
async def delete_import(import_id: int, request: Request,
                        session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    imp = await session.get(JourneyImport, import_id)
    if not imp:
        raise HTTPException(status_code=404, detail="Import not found")
    project = await project_for(session, principal, imp.project_id, "editor")
    await audit_record(session, entity_type="journey_import", entity_id=imp.id, action="delete",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"filename": imp.filename})
    await session.delete(imp)
    await session.commit()
    return {"deleted": import_id}


async def _config(session: AsyncSession, project_id: int) -> Optional[JourneyConfig]:
    return (await session.exec(select(JourneyConfig).where(
        JourneyConfig.project_id == project_id))).first()


class RulesIn(BaseModel):
    path_rules: List[Dict[str, Any]] = []


@router.get("/projects/{project_id}/journey-rules")
async def get_rules(project_id: int, session: AsyncSession = Depends(get_session),
                    principal: AuthPrincipal = Depends(get_current_principal)):
    await project_for(session, principal, project_id)
    cfg = await _config(session, project_id)
    return {"path_rules": (cfg.path_rules if cfg else None) or [],
            "max_rules": jc.MAX_RULES}


@router.put("/projects/{project_id}/journey-rules")
async def put_rules(project_id: int, body: RulesIn, request: Request,
                    session: AsyncSession = Depends(get_session),
                    principal: AuthPrincipal = Depends(get_current_principal)):
    project = await project_for(session, principal, project_id, "editor")
    try:
        rules = jc.validate_rules(body.path_rules)
    except jc.JourneyImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    cfg = await _config(session, project_id) or JourneyConfig(project_id=project_id)
    cfg.path_rules = rules
    cfg.updated_by_id = principal.user.id
    cfg.updated_at = datetime.utcnow()
    session.add(cfg)
    await audit_record(session, entity_type="journey_rules", entity_id=project_id, action="update",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"rules": len(rules)})
    await session.commit()
    return {"path_rules": rules, "max_rules": jc.MAX_RULES}


async def _rules_and_covered(session: AsyncSession, project_id: int):
    cfg = await _config(session, project_id)
    rules = jc.compile_rules(cfg.path_rules if cfg else [])
    cases = (await session.exec(select(TestCase.id, TestCase.steps).where(
        TestCase.project_id == project_id))).all()
    return rules, jc.covered_keys(cases, rules)


async def _latest_import(session: AsyncSession, project_id: int,
                         import_id: Optional[int] = None) -> Optional[JourneyImport]:
    stmt = select(JourneyImport).where(JourneyImport.project_id == project_id)
    if import_id:
        stmt = stmt.where(JourneyImport.id == import_id)
    return (await session.exec(stmt.order_by(JourneyImport.id.desc()).limit(1))).first()


@router.get("/projects/{project_id}/journey-coverage")
async def journey_coverage(project_id: int, import_id: Optional[int] = None,
                           top: int = Query(50, ge=1, le=500),
                           session: AsyncSession = Depends(get_session),
                           principal: AuthPrincipal = Depends(get_current_principal)):
    await project_for(session, principal, project_id)
    imp = await _latest_import(session, project_id, import_id)
    if import_id and not imp:
        raise HTTPException(status_code=404, detail="Import not found")
    rules, covered = await _rules_and_covered(session, project_id)
    if not imp:
        return {"import": None, "report": None, "tested_keys": sorted(covered)}
    report = jc.compute_coverage(imp.rows or [], covered, rules, top=top)
    names = {}
    ids = {cid for e in report["covered"] + report["covered_low_traffic"] for cid in e["case_ids"]}
    for j in report["journeys"]:
        ids.update(cid for s in j["steps"] for cid in s["case_ids"])
    if ids:
        names = dict((await session.exec(select(TestCase.id, TestCase.name).where(
            TestCase.id.in_(ids), TestCase.project_id == project_id))).all())
    return {"import": _import_out(imp), "report": report, "case_names": names}


# ---------------------------------------------------------------------------
# Business journeys
# ---------------------------------------------------------------------------

class JourneyIn(BaseModel):
    name: str = PydField(min_length=1, max_length=120)
    description: Optional[str] = PydField(default=None, max_length=4000)
    owner_id: Optional[int] = None
    criticality: str = "medium"
    case_ids: List[int] = []
    requirement_refs: List[str] = []
    traffic_journey: Optional[str] = PydField(default=None, max_length=200)


class JourneyPatch(BaseModel):
    name: Optional[str] = PydField(default=None, min_length=1, max_length=120)
    description: Optional[str] = PydField(default=None, max_length=4000)
    owner_id: Optional[int] = None
    criticality: Optional[str] = None
    case_ids: Optional[List[int]] = None
    requirement_refs: Optional[List[str]] = None
    traffic_journey: Optional[str] = PydField(default=None, max_length=200)


async def _check_journey_fields(session: AsyncSession, project_id: int, data: Dict[str, Any]) -> None:
    if "criticality" in data and data["criticality"] is not None:
        if data["criticality"] not in bj.CRITICALITIES:
            raise HTTPException(status_code=400,
                                detail=f"criticality must be one of {', '.join(bj.CRITICALITIES)}")
    if data.get("case_ids") is not None:
        ids = list(dict.fromkeys(data["case_ids"]))
        if len(ids) > bj.MAX_JOURNEY_CASES:
            raise HTTPException(status_code=400,
                                detail=f"at most {bj.MAX_JOURNEY_CASES} cases per journey")
        if ids:
            found = set((await session.exec(select(TestCase.id).where(
                TestCase.id.in_(ids), TestCase.project_id == project_id))).all())
            missing = [i for i in ids if i not in found]
            if missing:
                raise HTTPException(status_code=400,
                                    detail=f"case_ids not in this project: {missing[:20]}")
        data["case_ids"] = ids
    if data.get("requirement_refs") is not None:
        try:
            data["requirement_refs"] = bj.clean_refs(data["requirement_refs"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if data.get("owner_id"):
        # The owner must be able to see the project they are named owner of.
        if not await access_service.has_project_access(data["owner_id"], project_id, session):
            raise HTTPException(status_code=400, detail="owner has no access to this project")
    if "traffic_journey" in data:
        data["traffic_journey"] = (data["traffic_journey"] or "").strip() or None
    if "description" in data and data["description"] is not None:
        data["description"] = data["description"].strip() or None


def _journey_base(j: BusinessJourney) -> Dict[str, Any]:
    return {"id": j.id, "project_id": j.project_id, "name": j.name, "description": j.description,
            "owner_id": j.owner_id, "criticality": j.criticality, "case_ids": j.case_ids or [],
            "requirement_refs": j.requirement_refs or [], "traffic_journey": j.traffic_journey,
            "created_at": j.created_at.isoformat() if j.created_at else None,
            "updated_at": j.updated_at.isoformat() if j.updated_at else None}


HEALTH_WINDOW_DAYS = 30
MAX_HEALTH_RESULTS = 20000


@router.get("/projects/{project_id}/business-journeys")
async def journey_board(project_id: int, session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    """The board: every journey with health, last run, trend, requirement
    coverage and traffic from the latest analytics import."""
    await project_for(session, principal, project_id)
    journeys = (await session.exec(select(BusinessJourney).where(
        BusinessJourney.project_id == project_id).order_by(BusinessJourney.name))).all()
    all_ids = {cid for j in journeys for cid in (j.case_ids or [])}
    now = datetime.utcnow()

    cases: Dict[int, TestCase] = {}
    history: Dict[int, List[Dict[str, Any]]] = {}
    quarantined: set = set()
    links: Dict[int, List[str]] = {}
    if all_ids:
        cases = {c.id: c for c in (await session.exec(select(TestCase).where(
            TestCase.id.in_(all_ids), TestCase.project_id == project_id))).all()}
        if cases:
            rows = (await session.exec(
                select(TestCaseResult.test_case_id, TestCaseResult.status, TestCaseResult.is_flaky,
                       TestRun.id, TestRun.created_at)
                .join(TestRun, TestRun.id == TestCaseResult.test_run_id)
                .where(TestCaseResult.test_case_id.in_(cases.keys()),
                       TestRun.project_id == project_id, counted(),
                       TestRun.created_at >= now - timedelta(days=HEALTH_WINDOW_DAYS))
                .order_by(TestRun.created_at.desc(), TestCaseResult.id.desc())
                .limit(MAX_HEALTH_RESULTS))).all()
            for cid, status, flaky, run_id, created in rows:
                history.setdefault(cid, []).append(
                    {"status": status, "is_flaky": flaky, "run_id": run_id, "created_at": created})
            from app.services.flake_policy import active_quarantine_clause
            quarantined = set((await session.exec(select(FlakeRecord.test_case_id).where(
                FlakeRecord.test_case_id.in_(cases.keys()), active_quarantine_clause()))).all())
            for cid, ref in (await session.exec(select(RequirementLink.test_case_id, RequirementLink.ref)
                                                .where(RequirementLink.test_case_id.in_(cases.keys())))).all():
                links.setdefault(cid, []).append(ref)

    # Requirement refs: declared on the journey + linked to its cases.
    all_refs = {r for j in journeys for r in (j.requirement_refs or [])} | {
        r for refs in links.values() for r in refs}
    req_info: Dict[str, Dict[str, Any]] = {}
    ref_cases: Dict[str, set] = {}
    if all_refs:
        for key, title, url in (await session.exec(select(
                Requirement.external_key, Requirement.title, Requirement.url).where(
                Requirement.project_id == project_id, Requirement.external_key.in_(all_refs)))).all():
            req_info[key] = {"title": title, "url": url}
        for cid, ref in (await session.exec(select(RequirementLink.test_case_id, RequirementLink.ref).where(
                RequirementLink.project_id == project_id, RequirementLink.ref.in_(all_refs)))).all():
            ref_cases.setdefault(ref, set()).add(cid)

    imp = await _latest_import(session, project_id)
    rules, covered = (await _rules_and_covered(session, project_id)) if imp else ([], {})

    owner_ids = {j.owner_id for j in journeys if j.owner_id}
    owners = {}
    if owner_ids:
        owners = {u.id: {"id": u.id, "name": u.full_name, "email": u.email}
                  for u in (await session.exec(select(User).where(User.id.in_(owner_ids)))).all()}

    out = []
    for j in journeys:
        ids = [cid for cid in (j.case_ids or []) if cid in cases]
        health = bj.journey_health(ids, {cid: cases[cid].name for cid in ids}, history,
                                   quarantined, now)
        states = {c["id"]: c["state"] for c in health["cases"]}
        refs = list(dict.fromkeys((j.requirement_refs or []) + [r for cid in ids for r in links.get(cid, [])]))
        req_rows = []
        for ref in refs:
            # Coverage within the journey: its own cases linked to the ref.
            linked = sorted(ref_cases.get(ref, set()) & set(ids))
            st = [states[cid] for cid in linked]
            req_rows.append({"ref": ref, **req_info.get(ref, {"title": None, "url": None}),
                             "cases": len(linked),
                             "project_cases": len(ref_cases.get(ref, set())),
                             "status": bj.rollup(st) if st else "uncovered"})
        traffic = None
        if imp:
            if j.traffic_journey:
                traffic = jc.journey_traffic(imp.rows or [], j.traffic_journey, covered, rules)
                if traffic:
                    traffic["source"] = "journey"
            if traffic is None:
                keys = set()
                for cid in ids:
                    for url in jc.goto_urls(cases[cid].steps):
                        p = jc.normalize_path(url)
                        if p:
                            keys.add(jc.canonical_key(p, rules))
                views = jc.keys_traffic(imp.rows or [], keys, rules)
                traffic = {"source": "pages", "views": views,
                           "share_pct": round(100.0 * views / imp.total_views, 1) if imp.total_views else None,
                           "pages": len(keys)}
            traffic["import_id"] = imp.id
        out.append({**_journey_base(j), "owner": owners.get(j.owner_id), **health,
                    "missing_case_ids": [cid for cid in (j.case_ids or []) if cid not in cases],
                    "requirements": req_rows,
                    "requirements_covered": sum(1 for r in req_rows if r["cases"]),
                    "traffic": traffic})
    return {"journeys": out, "window_days": HEALTH_WINDOW_DAYS,
            "criticalities": list(bj.CRITICALITIES),
            "latest_import_id": imp.id if imp else None}


@router.post("/projects/{project_id}/business-journeys")
async def create_journey(project_id: int, body: JourneyIn, request: Request,
                         session: AsyncSession = Depends(get_session),
                         principal: AuthPrincipal = Depends(get_current_principal)):
    project = await project_for(session, principal, project_id, "editor")
    data = body.model_dump()
    data["name"] = data["name"].strip()
    await _check_journey_fields(session, project_id, data)
    if (await session.exec(select(BusinessJourney.id).where(
            BusinessJourney.project_id == project_id, BusinessJourney.name == data["name"]))).first():
        raise HTTPException(status_code=409, detail="a journey with this name already exists")
    j = BusinessJourney(project_id=project_id, created_by_id=principal.user.id, **data)
    session.add(j)
    await session.flush()
    await audit_record(session, entity_type="business_journey", entity_id=j.id, action="create",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"name": j.name, "cases": len(j.case_ids or [])})
    await session.commit()
    await session.refresh(j)
    return _journey_base(j)


async def _journey_for(session, principal, journey_id: int, min_role="viewer"):
    j = await session.get(BusinessJourney, journey_id)
    if not j:
        raise HTTPException(status_code=404, detail="Journey not found")
    project = await project_for(session, principal, j.project_id, min_role)
    return j, project


@router.patch("/business-journeys/{journey_id}")
async def update_journey(journey_id: int, body: JourneyPatch, request: Request,
                         session: AsyncSession = Depends(get_session),
                         principal: AuthPrincipal = Depends(get_current_principal)):
    j, project = await _journey_for(session, principal, journey_id, "editor")
    data = body.model_dump(exclude_unset=True)
    if "name" in data:
        if not data["name"]:
            raise HTTPException(status_code=400, detail="name is required")
        data["name"] = data["name"].strip()
        clash = (await session.exec(select(BusinessJourney.id).where(
            BusinessJourney.project_id == j.project_id, BusinessJourney.name == data["name"],
            BusinessJourney.id != j.id))).first()
        if clash:
            raise HTTPException(status_code=409, detail="a journey with this name already exists")
    await _check_journey_fields(session, j.project_id, data)
    for k, v in data.items():
        if k == "criticality" and v is None:
            continue
        setattr(j, k, v)
    j.updated_at = datetime.utcnow()
    session.add(j)
    await audit_record(session, entity_type="business_journey", entity_id=j.id, action="update",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={k: (len(v) if isinstance(v, list) else v) for k, v in data.items()
                                if k != "description"})
    await session.commit()
    await session.refresh(j)
    return _journey_base(j)


@router.delete("/business-journeys/{journey_id}")
async def delete_journey(journey_id: int, request: Request,
                         session: AsyncSession = Depends(get_session),
                         principal: AuthPrincipal = Depends(get_current_principal)):
    j, project = await _journey_for(session, principal, journey_id, "editor")
    await audit_record(session, entity_type="business_journey", entity_id=j.id, action="delete",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"name": j.name})
    await session.delete(j)
    await session.commit()
    return {"deleted": journey_id}


# ---------------------------------------------------------------------------
# Feature flags: catalogue + coverage
# ---------------------------------------------------------------------------

class FlagIn(BaseModel):
    name: str
    values: List[Any] = []
    description: Optional[str] = PydField(default=None, max_length=1000)


class FlagPatch(BaseModel):
    values: Optional[List[Any]] = None
    description: Optional[str] = PydField(default=None, max_length=1000)


def _flag_out(f: FlagCatalogEntry) -> Dict[str, Any]:
    return {"id": f.id, "project_id": f.project_id, "name": f.name, "values": f.values or [],
            "description": f.description,
            "updated_at": f.updated_at.isoformat() if f.updated_at else None}


@router.get("/projects/{project_id}/flag-catalog")
async def list_flags(project_id: int, session: AsyncSession = Depends(get_session),
                     principal: AuthPrincipal = Depends(get_current_principal)):
    await project_for(session, principal, project_id)
    rows = (await session.exec(select(FlagCatalogEntry).where(
        FlagCatalogEntry.project_id == project_id).order_by(FlagCatalogEntry.name))).all()
    return {"flags": [_flag_out(f) for f in rows]}


MAX_CATALOG_FLAGS = 500


@router.post("/projects/{project_id}/flag-catalog")
async def create_flag(project_id: int, body: FlagIn, request: Request,
                      session: AsyncSession = Depends(get_session),
                      principal: AuthPrincipal = Depends(get_current_principal)):
    project = await project_for(session, principal, project_id, "editor")
    try:
        name = fc.validate_flag_name(body.name)
        values = fc.validate_catalog_values(body.values)
    except fc.FlagError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if (await session.exec(select(FlagCatalogEntry.id).where(
            FlagCatalogEntry.project_id == project_id, FlagCatalogEntry.name == name))).first():
        raise HTTPException(status_code=409, detail="this flag is already in the catalogue")
    count = (await session.exec(select(func.count()).select_from(FlagCatalogEntry).where(
        FlagCatalogEntry.project_id == project_id))).one()
    if count >= MAX_CATALOG_FLAGS:
        raise HTTPException(status_code=409, detail=f"at most {MAX_CATALOG_FLAGS} catalogue flags")
    f = FlagCatalogEntry(project_id=project_id, name=name, values=values,
                         description=(body.description or "").strip() or None,
                         created_by_id=principal.user.id)
    session.add(f)
    await session.flush()
    await audit_record(session, entity_type="flag_catalog", entity_id=f.id, action="create",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"name": name, "values": values})
    await session.commit()
    await session.refresh(f)
    return _flag_out(f)


async def _flag_for(session, principal, flag_id: int, min_role="viewer"):
    f = await session.get(FlagCatalogEntry, flag_id)
    if not f:
        raise HTTPException(status_code=404, detail="Flag not found")
    project = await project_for(session, principal, f.project_id, min_role)
    return f, project


@router.patch("/flag-catalog/{flag_id}")
async def update_flag(flag_id: int, body: FlagPatch, request: Request,
                      session: AsyncSession = Depends(get_session),
                      principal: AuthPrincipal = Depends(get_current_principal)):
    f, project = await _flag_for(session, principal, flag_id, "editor")
    data = body.model_dump(exclude_unset=True)
    if "values" in data:
        try:
            f.values = fc.validate_catalog_values(data["values"])
        except fc.FlagError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if "description" in data:
        f.description = (data["description"] or "").strip() or None
    f.updated_at = datetime.utcnow()
    session.add(f)
    await audit_record(session, entity_type="flag_catalog", entity_id=f.id, action="update",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"values": f.values})
    await session.commit()
    await session.refresh(f)
    return _flag_out(f)


@router.delete("/flag-catalog/{flag_id}")
async def delete_flag(flag_id: int, request: Request,
                      session: AsyncSession = Depends(get_session),
                      principal: AuthPrincipal = Depends(get_current_principal)):
    f, project = await _flag_for(session, principal, flag_id, "editor")
    await audit_record(session, entity_type="flag_catalog", entity_id=f.id, action="delete",
                       workspace_id=project.workspace_id, principal=principal, request=request,
                       changes={"name": f.name})
    await session.delete(f)
    await session.commit()
    return {"deleted": flag_id}


MAX_FLAG_RUNS = 5000


@router.get("/projects/{project_id}/flag-coverage")
async def flag_coverage(project_id: int, days: int = Query(30, ge=1, le=365),
                        session: AsyncSession = Depends(get_session),
                        principal: AuthPrincipal = Depends(get_current_principal)):
    await project_for(session, principal, project_id)
    since = datetime.utcnow() - timedelta(days=days)
    runs = (await session.exec(
        select(TestRun.id, TestRun.flags, TestRun.status, TestRun.created_at, TestRun.test_case_id)
        .where(TestRun.project_id == project_id, TestRun.created_at >= since, counted(),
               TestRun.flags.isnot(None), cast(TestRun.flags, String) != "null")
        .order_by(TestRun.created_at.desc()).limit(MAX_FLAG_RUNS + 1))).all()
    truncated = len(runs) > MAX_FLAG_RUNS
    runs = runs[:MAX_FLAG_RUNS]
    # Suite-level runs execute many cases: count the cases their results name.
    cases_by_run: Dict[int, set] = {r[0]: ({r[4]} if r[4] else set()) for r in runs}
    suite_runs = [r[0] for r in runs if not r[4]]
    for i in range(0, len(suite_runs), 1000):
        chunk = suite_runs[i:i + 1000]
        for run_id, cid in (await session.exec(select(TestCaseResult.test_run_id, TestCaseResult.test_case_id)
                                               .where(TestCaseResult.test_run_id.in_(chunk),
                                                      TestCaseResult.test_case_id.isnot(None)))).all():
            cases_by_run[run_id].add(cid)
    catalog = [_flag_out(f) for f in (await session.exec(select(FlagCatalogEntry).where(
        FlagCatalogEntry.project_id == project_id))).all()]
    report = fc.aggregate(
        [{"id": rid, "flags": flags, "status": status, "created_at": created,
          "case_ids": cases_by_run.get(rid, set())}
         for rid, flags, status, created, _ in runs], catalog)
    return {**report, "days": days, "truncated": truncated}
