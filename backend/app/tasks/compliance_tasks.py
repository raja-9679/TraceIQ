"""Evidence-pack generation (WP13a).

Always a Celery task, never in the request: a pack walks the whole audit
chain and every project's findings, which for a large workspace takes longer
than a request should hold a connection, and doing it one way only means the
small and large paths cannot drift apart. The zip goes to a spooled temp file
(memory up to SPOOL_BYTES, then disk), is uploaded to the object store under
compliance/ws<id>/ (covered by the workspace purge), and is downloaded later
through a short-lived presigned URL.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import tempfile
import uuid
from datetime import datetime

from app.core.celery_app import celery_app
from app.core.config import db_url_for, settings
from app.services.compliance import workspace_prefix

logger = logging.getLogger(__name__)

SPOOL_BYTES = 8 * 1024 * 1024


def pack_object_key(workspace_id: int, pack_id: int) -> str:
    return f"{workspace_prefix(workspace_id)}evidence-packs/{pack_id}-{uuid.uuid4().hex[:12]}.zip"


async def run_build(session, pack_id: int, *, store=None) -> None:
    """Build + upload one pack with the given async session (tests call this)."""
    from app.core.secrets import purpose_keys
    from app.core.storage import object_store
    from app.models import EvidencePack, User
    from app.services import audit
    from app.services.evidence_pack import KEY_PURPOSE, build_pack

    store = store or object_store
    pack = await session.get(EvidencePack, pack_id)
    if not pack or pack.status not in ("queued", "building"):
        return
    pack.status = "building"
    session.add(pack)
    await session.commit()
    ws_id, requester_id = pack.workspace_id, pack.requested_by_id
    requester = await session.get(User, requester_id)
    try:
        with tempfile.SpooledTemporaryFile(max_size=SPOOL_BYTES) as spool:
            manifest = await build_pack(
                session, pack, spool, key=purpose_keys(KEY_PURPOSE)[0],
                generated_by={"user_id": requester_id,
                              "email": requester.email if requester else None})
            spool.seek(0)
            digest, size = hashlib.sha256(), 0
            for chunk in iter(lambda: spool.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
            spool.seek(0)
            key = pack_object_key(ws_id, pack_id)
            await asyncio.to_thread(store.upload_fileobj, spool, key, "application/zip")
        pack = await session.get(EvidencePack, pack_id)
        pack.status, pack.object_key, pack.size_bytes = "ready", key, size
        pack.sha256, pack.manifest = digest.hexdigest(), manifest
        pack.file_count, pack.completed_at = len(manifest["files"]), datetime.utcnow()
        session.add(pack)
        await audit.record(session, entity_type="evidence_pack", entity_id=pack_id, action="ready",
                           user_id=requester_id, workspace_id=ws_id,
                           changes={"files": pack.file_count, "size_bytes": size,
                                    "sha256": pack.sha256,
                                    "manifest_signature": manifest["signature"]["value"]})
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("[EvidencePack] build %s failed", pack_id)
        await session.rollback()
        pack = await session.get(EvidencePack, pack_id)
        if pack:
            pack.status, pack.completed_at = "failed", datetime.utcnow()
            pack.error = f"{type(exc).__name__}: {str(exc)[:200]}"
            session.add(pack)
            await audit.record(session, entity_type="evidence_pack", entity_id=pack_id,
                               action="failed", user_id=requester_id, workspace_id=ws_id,
                               changes={"error": type(exc).__name__})
            await session.commit()


async def _build_async(pack_id: int) -> None:
    # A private engine per call: the app's pooled engine is bound to the event
    # loop it was first used on, and every asyncio.run is a new loop.
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from sqlmodel.ext.asyncio.session import AsyncSession
    engine = create_async_engine(db_url_for(settings.DATABASE_URL, sync=False), poolclass=NullPool)
    try:
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as session:
            await run_build(session, pack_id)
    finally:
        await engine.dispose()


@celery_app.task(name="app.tasks.compliance_tasks.build_evidence_pack")
def build_evidence_pack(pack_id: int) -> None:
    asyncio.run(_build_async(pack_id))
