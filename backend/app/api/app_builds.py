"""Mobile app-build registry — Phase MOB (native app testing).

Uploaded APK/AAB/IPA binaries that `mobile_appium` runs install and test.
Bytes go to MinIO under `app-builds/{project_id}/`; rows here are the
registry. Dispatch presigns `file_key` into the job payload so the mobile
worker can download the binary (see `worker.py:_load_mobile_app`).

Surface:
    POST   /api/projects/{project_id}/app-builds     upload (multipart)
    GET    /api/projects/{project_id}/app-builds     list
    GET    /api/app-builds/{id}                      detail + download URL
    DELETE /api/app-builds/{id}
"""
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.auth import AuthPrincipal, get_current_principal
from app.core.database import get_session
from app.core.storage import minio_client
from app.models import MobileAppBuild, MobileAppBuildRead, Project
from app.services.access_service import access_service

router = APIRouter()

_ALLOWED_EXTENSIONS = {
    "android": (".apk", ".aab"),
    "ios": (".ipa",),
}
_CONTENT_TYPE = "application/octet-stream"


def _to_read(build: MobileAppBuild, with_url: bool = False) -> MobileAppBuildRead:
    read = MobileAppBuildRead(
        id=build.id,
        project_id=build.project_id,
        platform=build.platform,
        app_name=build.app_name,
        version_name=build.version_name,
        build_number=build.build_number,
        package_id=build.package_id,
        file_size=build.file_size,
        original_filename=build.original_filename,
        notes=build.notes,
        created_at=build.created_at,
    )
    if with_url:
        try:
            read.download_url = minio_client.get_presigned_url(build.file_key)
        except Exception:
            read.download_url = None
    return read


@router.post("/projects/{project_id}/app-builds", response_model=MobileAppBuildRead)
async def upload_app_build(
    project_id: int,
    file: UploadFile = File(...),
    platform: str = Form("android"),
    app_name: Optional[str] = Form(None),
    version_name: Optional[str] = Form(None),
    build_number: Optional[str] = Form(None),
    package_id: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> MobileAppBuildRead:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not await access_service.has_project_access(
        principal.user.id, project_id, session, min_role="editor"
    ):
        raise HTTPException(status_code=403, detail="Editor access required")

    platform = (platform or "android").lower()
    if platform not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="platform must be 'android' or 'ios'")

    filename = file.filename or "app.bin"
    if not filename.lower().endswith(_ALLOWED_EXTENSIONS[platform]):
        allowed = ", ".join(_ALLOWED_EXTENSIONS[platform])
        raise HTTPException(
            status_code=400,
            detail=f"File extension does not match platform '{platform}' (expected: {allowed})")

    object_key = f"app-builds/{project_id}/{uuid.uuid4()}/{filename}"
    try:
        minio_client.upload_fileobj(file.file, object_key, content_type=_CONTENT_TYPE)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Upload to object storage failed: {e}")

    # UploadFile.size is populated by Starlette when the client sent a length.
    size = getattr(file, "size", None)

    build = MobileAppBuild(
        project_id=project_id,
        platform=platform,
        app_name=app_name or filename.rsplit(".", 1)[0],
        version_name=version_name,
        build_number=build_number,
        package_id=package_id,
        file_key=object_key,
        file_size=size,
        original_filename=filename,
        notes=notes,
        created_by_id=principal.user.id,
    )
    session.add(build)
    await session.commit()
    await session.refresh(build)
    return _to_read(build)


@router.get("/projects/{project_id}/app-builds", response_model=List[MobileAppBuildRead])
async def list_app_builds(
    project_id: int,
    platform: Optional[str] = None,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> List[MobileAppBuildRead]:
    if not await access_service.has_project_access(
        principal.user.id, project_id, session, min_role="viewer"
    ):
        raise HTTPException(status_code=403, detail="No access to this project")

    query = select(MobileAppBuild).where(MobileAppBuild.project_id == project_id)
    if platform:
        query = query.where(MobileAppBuild.platform == platform.lower())
    result = await session.exec(query.order_by(MobileAppBuild.created_at.desc()))
    return [_to_read(b) for b in result.all()]


@router.get("/app-builds/{build_id}", response_model=MobileAppBuildRead)
async def get_app_build(
    build_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> MobileAppBuildRead:
    build = await session.get(MobileAppBuild, build_id)
    if not build:
        raise HTTPException(status_code=404, detail="App build not found")
    if not await access_service.has_project_access(
        principal.user.id, build.project_id, session, min_role="viewer"
    ):
        raise HTTPException(status_code=403, detail="No access to this project")
    return _to_read(build, with_url=True)


@router.delete("/app-builds/{build_id}")
async def delete_app_build(
    build_id: int,
    principal: AuthPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    build = await session.get(MobileAppBuild, build_id)
    if not build:
        raise HTTPException(status_code=404, detail="App build not found")
    if not await access_service.has_project_access(
        principal.user.id, build.project_id, session, min_role="editor"
    ):
        raise HTTPException(status_code=403, detail="Editor access required")

    try:
        minio_client.delete_object(build.file_key)
    except Exception as e:
        # The registry row is the source of truth; a failed object delete
        # must not orphan the row silently — surface it but proceed.
        print(f"[AppBuilds] Failed to delete object {build.file_key}: {e}")

    await session.delete(build)
    await session.commit()
    return {"deleted": build_id}
