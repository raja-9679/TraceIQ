"""Create an external tracker ticket from a run and attach its artifacts.

Runs as a Celery task because artifact upload (trace/video can be large) is slow
and should not block the request. The ticket row is created PENDING by the API;
this task creates the issue, then downloads the run's MinIO artifacts and uploads
them to the tracker (or, for trackers without an attachment API like GitHub,
appends signed links to the body).
"""
import logging
import os
from typing import List, Tuple

from sqlmodel import Session, create_engine

from app.core.celery_app import celery_app
from app.core.config import db_url_for, settings
from app.core.secrets import decrypt_secret
from app.core.storage import minio_client
from app.models import IssueTicket, IssueTrackerConfig, TestRun
from app.services.issue_trackers import get_provider, IssueTrackerError

logger = logging.getLogger(__name__)

sync_engine = create_engine(db_url_for(settings.DATABASE_URL, sync=True), echo=False)

_CONTENT_TYPES = {
    ".zip": "application/zip", ".webm": "video/webm", ".mp4": "video/mp4",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".html": "text/html", ".json": "application/json", ".txt": "text/plain",
}


def _classify(key: str) -> str:
    k = key.lower()
    if k.endswith(".zip") or "trace" in k:
        return "trace"
    if k.endswith((".webm", ".mp4")):
        return "video"
    if k.endswith((".png", ".jpg", ".jpeg")):
        return "screenshot"
    return "other"


def _list_artifacts(run_id: int) -> List[str]:
    """Object keys under the run's MinIO prefix."""
    try:
        resp = minio_client.s3.list_objects_v2(
            Bucket=minio_client.bucket, Prefix=f"runs/{run_id}/")
        return [o["Key"] for o in resp.get("Contents", [])]
    except Exception as e:  # noqa: BLE001
        logger.warning("[Ticket] could not list artifacts for run %s: %s", run_id, e)
        return []


def _fetch(key: str) -> bytes:
    obj = minio_client.s3.get_object(Bucket=minio_client.bucket, Key=key)
    return obj["Body"].read()


def _selected_keys(run_id: int, attach_trace: bool, attach_video: bool,
                   attach_screenshots: bool) -> List[Tuple[str, str]]:
    wanted = set()
    if attach_trace:
        wanted.add("trace")
    if attach_video:
        wanted.add("video")
    if attach_screenshots:
        wanted.add("screenshot")
    out = []
    for key in _list_artifacts(run_id):
        kind = _classify(key)
        if kind in wanted:
            out.append((key, kind))
    return out


@celery_app.task(bind=True, max_retries=0)
def create_issue_ticket(self, ticket_id: int, description: str = "", priority: str = None,
                        attach_trace: bool = True, attach_video: bool = True,
                        attach_screenshots: bool = True):
    with Session(sync_engine) as s:
        ticket = s.get(IssueTicket, ticket_id)
        if not ticket:
            return
        config = s.get(IssueTrackerConfig, ticket.config_id)
        if not config:
            ticket.status = "error"; ticket.error = "Tracker config not found"; s.add(ticket); s.commit(); return

        try:
            secret = decrypt_secret(config.auth_secret_encrypted)
            provider = get_provider(config.provider, config.base_url, config.auth_user, secret, config.settings)

            # For trackers without attachments, embed signed artifact links.
            body = description or ""
            keys = _selected_keys(ticket.run_id, attach_trace, attach_video, attach_screenshots) if ticket.run_id else []
            if keys and not provider.supports_attachments:
                links = []
                for key, kind in keys:
                    try:
                        links.append(f"- {kind}: {minio_client.get_presigned_url(key)}")
                    except Exception:  # noqa: BLE001
                        pass
                if links:
                    body = f"{body}\n\n**Artifacts:**\n" + "\n".join(links)

            created = provider.create_issue(ticket.summary, body, priority)
            ticket.external_key = created.get("key")
            ticket.url = created.get("url")
            ticket.status = "created"
            s.add(ticket); s.commit()

            if keys and provider.supports_attachments:
                ticket.attachments_total = len(keys)
                s.add(ticket); s.commit()
                uploaded = 0
                for key, _kind in keys:
                    try:
                        content = _fetch(key)
                        fname = os.path.basename(key)
                        ext = os.path.splitext(fname)[1].lower()
                        provider.attach_file(fname, content, _CONTENT_TYPES.get(ext, "application/octet-stream"))
                        uploaded += 1
                    except Exception as e:  # noqa: BLE001
                        logger.warning("[Ticket] attach %s failed: %s", key, e)
                ticket.attachments_uploaded = uploaded
                s.add(ticket); s.commit()

            logger.info("[Ticket] %s created %s (%s/%s attachments)",
                        ticket_id, ticket.external_key, ticket.attachments_uploaded, ticket.attachments_total)

        except IssueTrackerError as e:
            ticket.status = "error"; ticket.error = str(e)[:1000]; s.add(ticket); s.commit()
        except Exception as e:  # noqa: BLE001
            ticket.status = "error"; ticket.error = f"Unexpected: {e}"[:1000]; s.add(ticket); s.commit()
