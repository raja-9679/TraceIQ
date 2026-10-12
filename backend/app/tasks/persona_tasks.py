"""Persona session-refresh task.

Runs the persona's `login_steps` on an execution worker and stores the
resulting `storageState` (encrypted) on the persona.

The job rides the normal `jobs:pending` stream as `job_type=persona_refresh`
— the same side-channel shape as Mode-2 discovery jobs: no TestRun, the
worker parks its result under a short-lived Redis key and this task
long-polls for it. (This used to POST to a `/persona-refresh` endpoint on the
legacy execution-engine that was never implemented, and the legacy engine is
not part of the shipped deployments at all, so refresh silently never ran.)

Idempotent: on any failure `last_refreshed_at` is left untouched and the
next refresh tries again.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from typing import Any, Dict

import redis
from sqlmodel import Session, create_engine

from app.core.celery_app import celery_app
from app.core.config import db_url_for, settings
from app.models import Persona
from app.core.secrets import encrypt_json

_sync_db_url = db_url_for(settings.DATABASE_URL, sync=True)
_engine = create_engine(_sync_db_url, echo=False)

JOBS_STREAM = "jobs:pending"
CONSUMER_GROUP = "execution-workers"
RESULT_KEY = "persona_refresh:result:{}"
# Login flows are short; a worker that has not answered in this window is
# busy or down, and the next scheduled refresh will try again.
POLL_SECONDS = 120


def build_refresh_job(persona: Persona, secrets: Dict[str, str] | None) -> Dict[str, Any]:
    """The job payload a worker executes. Pure so it can be unit-tested."""
    refresh_id = str(uuid.uuid4())
    job_settings: Dict[str, Any] = {"headers": dict(persona.auth_headers or {})}
    if secrets:
        # login_steps normally reference {{secret.PASSWORD}} rather than
        # storing a plaintext password on the persona.
        job_settings["secrets"] = secrets
    return {
        "job_id": refresh_id,
        "refresh_id": refresh_id,
        "job_type": "persona_refresh",
        "run_id": 0,
        "persona_id": persona.id,
        "browser": "chromium",
        "login_steps": list(persona.login_steps or []),
        "settings": job_settings,
    }


@celery_app.task(name="app.tasks.persona_tasks.refresh_persona_session", bind=True, max_retries=2)
def refresh_persona_session(self, persona_id: int) -> Dict[str, Any]:
    with Session(_engine) as session:
        persona = session.get(Persona, persona_id)
        if not persona:
            return {"status": "not_found", "persona_id": persona_id}
        if not persona.login_steps:
            return {"status": "no_login_steps", "persona_id": persona_id}

        secrets = None
        if persona.project_id:
            from app.worker import _load_secrets
            secrets = _load_secrets(persona.project_id, session)
        job = build_refresh_job(persona, secrets)

    rc = redis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)
    try:
        rc.xgroup_create(JOBS_STREAM, CONSUMER_GROUP, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise
    rc.xadd(JOBS_STREAM, {"job_id": job["job_id"], "run_id": "0",
                          "payload": json.dumps(job)})

    key = RESULT_KEY.format(job["refresh_id"])
    raw = None
    for _ in range(POLL_SECONDS):
        raw = rc.get(key)
        if raw:
            # The value is a live session; don't leave it in Redis longer
            # than it takes to read it.
            rc.delete(key)
            break
        time.sleep(1)
    if not raw:
        print(f"[PersonaRefresh] persona {persona_id}: no worker result "
              f"within {POLL_SECONDS}s")
        return {"status": "timeout", "persona_id": persona_id}

    result = json.loads(raw)
    new_state = result.get("storage_state")
    if result.get("status") != "ok" or not new_state:
        print(f"[PersonaRefresh] persona {persona_id}: login failed: "
              f"{result.get('error')}")
        return {"status": "login_failed", "persona_id": persona_id,
                "error": result.get("error")}

    with Session(_engine) as session:
        persona = session.get(Persona, persona_id)
        if not persona:
            return {"status": "not_found", "persona_id": persona_id}
        persona.session_state = encrypt_json(new_state)
        persona.last_refreshed_at = datetime.utcnow()
        session.add(persona)
        session.commit()
    return {"status": "refreshed", "persona_id": persona_id}
