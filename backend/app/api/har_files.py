"""Uploaded HAR files for the `har-replay` step (WP15d, H7).

    POST   /api/projects/{id}/har-files          multipart `file` (+ `name`) — editor; sniffed, redacted, stored
    GET    /api/projects/{id}/har-files          list (viewer)
    DELETE /api/har-files/{id}                   editor
    GET    /api/internal/har-files/{id}?run_id=&test_case_id=   worker (X-Worker-Secret)

Upload and delete are audited (entity_type `har_file`). Rules in
app/services/har_files.py.
"""
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.flake_records import _check_key_scope
from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.core.storage import minio_client
from app.core.worker_auth import require_worker
from app.models import HarArchive, Project, TestCase, TestRun
from app.services import har_files, run_status
from app.services.access_service import access_service
from app.services.audit import record as audit_record
from app.services.data_policy import resolve_for_project

router = APIRouter()


def _read(h: HarArchive) -> dict:
    return {"id": h.id, "project_id": h.project_id, "name": h.name,
            "size_bytes": h.size_bytes, "entry_count": h.entry_count,
            "hosts": h.hosts or [], "sha256": h.sha256,
            "created_by_id": h.created_by_id, "created_at": h.created_at.isoformat() + "Z"}


async def _project(project_id: int, principal: AuthPrincipal, session: AsyncSession,
                   min_role: str) -> Project:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not await access_service.has_project_access(principal.user.id, project_id, session,
                                                   min_role=min_role):
        raise HTTPException(status_code=403,
                            detail="Editor access required" if min_role == "editor" else "Access denied")
    _check_key_scope(principal, project_id)
    return project


async def _bounded_read(file: UploadFile, limit: int) -> bytes:
    chunks, total = [], 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=413,
                                detail=f"HAR files are limited to {limit // (1024 * 1024)} MB")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/projects/{project_id}/har-files")
async def upload_har(
    project_id: int, request: Request,
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    project = await _project(project_id, principal, session, "editor")
    raw = await _bounded_read(file, har_files.MAX_HAR_BYTES)
    try:
        body, summary = har_files.prepare_upload(raw, resolve_for_project(project))
    except har_files.HarError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    label = (name or file.filename or "recording.har").strip()[:120] or "recording.har"
    key = har_files.object_key(project_id, uuid.uuid4().hex)
    import io
    try:
        minio_client.upload_fileobj(io.BytesIO(body), key, content_type="application/json")
    except Exception as exc:  # noqa: BLE001
        print(f"[HarFiles] upload to object storage failed: {exc}")
        raise HTTPException(status_code=502, detail="Upload to object storage failed")
    row = HarArchive(project_id=project_id, name=label, object_key=key,
                     created_by_id=principal.user.id, **summary)
    session.add(row)
    await session.flush()
    await audit_record(
        session, entity_type="har_file", entity_id=row.id, action="upload",
        workspace_id=project.workspace_id, principal=principal, request=request,
        changes={"project_id": project_id, "name": label, "entries": row.entry_count,
                 "size_bytes": row.size_bytes, "sha256": row.sha256})
    await session.commit()
    await session.refresh(row)
    return _read(row)


@router.get("/projects/{project_id}/har-files")
async def list_hars(
    project_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> List[dict]:
    await _project(project_id, principal, session, "viewer")
    rows = (await session.exec(select(HarArchive).where(HarArchive.project_id == project_id)
                               .order_by(HarArchive.created_at.desc()))).all()
    return [_read(r) for r in rows]


@router.delete("/har-files/{har_id}")
async def delete_har(
    har_id: int, request: Request,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(HarArchive, har_id)
    if not row or not await access_service.has_project_access(principal.user.id, row.project_id, session):
        raise HTTPException(status_code=404, detail="HAR file not found")
    project = await _project(row.project_id, principal, session, "editor")
    key, pid = row.object_key, project.id
    await audit_record(
        session, entity_type="har_file", entity_id=row.id, action="delete",
        workspace_id=project.workspace_id, principal=principal, request=request,
        changes={"project_id": row.project_id, "name": row.name, "sha256": row.sha256})
    await session.delete(row)
    await session.commit()
    # Only keys under this project's prefix are ours to delete.
    if key.startswith(har_files.project_prefix(pid)):
        try:
            minio_client.delete_object(key)
        except Exception as exc:  # noqa: BLE001 — the row is gone; the purge sweeps the prefix
            print(f"[HarFiles] could not delete {key}: {exc}")
    return {"deleted": har_id}


@router.get("/internal/har-files/{har_id}", dependencies=[Depends(require_worker)])
async def worker_fetch_har(
    har_id: int,
    run_id: int = Query(...),
    test_case_id: int = Query(...),
    session: AsyncSession = Depends(get_session),
):
    """The redacted HAR for a running case. Served only when the run is live
    and the case and the HAR are both in the run's project — the worker
    secret alone must not read any project's recordings."""
    run = await session.get(TestRun, run_id)
    if not run or run.finalized_at is not None or run.status in run_status.TERMINAL:
        raise HTTPException(status_code=404, detail="run is not live")
    case = await session.get(TestCase, test_case_id)
    row = await session.get(HarArchive, har_id)
    if (not case or case.project_id != run.project_id or not row
            or row.project_id != run.project_id):
        raise HTTPException(status_code=404, detail="HAR file not found for this run's project")
    try:
        data = minio_client.get_object_bytes(row.object_key, max_bytes=har_files.MAX_HAR_BYTES * 2)
    except Exception as exc:  # noqa: BLE001
        print(f"[HarFiles] read {row.object_key} failed: {exc}")
        raise HTTPException(status_code=502, detail="HAR file could not be read")
    return Response(content=data, media_type="application/json",
                    headers={"X-TraceIQ-Har-Sha256": row.sha256})
