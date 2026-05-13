"""Persona session-refresh task.

Runs the persona's `login_steps` via the legacy execution-engine HTTP path
(simpler than queuing through Redis Streams for a single throw-away job)
and stores the resulting `storageState`. Designed to be idempotent — if the
engine is unavailable, the persona's `last_refreshed_at` is left untouched
and the next scheduled refresh tries again.

This task is intentionally light on coupling: it just POSTs a one-shot job
payload to the execution-engine and writes the result back to the persona.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict

import requests
from sqlmodel import Session, create_engine

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models import Persona

_sync_db_url = settings.DATABASE_URL.replace("+asyncpg", "")
_engine = create_engine(_sync_db_url, echo=False)


@celery_app.task(name="app.tasks.persona_tasks.refresh_persona_session", bind=True, max_retries=2)
def refresh_persona_session(self, persona_id: int) -> Dict[str, Any]:
    with Session(_engine) as session:
        persona = session.get(Persona, persona_id)
        if not persona:
            return {"status": "not_found", "persona_id": persona_id}
        if not persona.login_steps:
            return {"status": "no_login_steps", "persona_id": persona_id}

        engine_url = settings.EXECUTION_ENGINE_URL.rstrip("/")
        if engine_url.endswith("/run"):
            engine_url = engine_url[: -len("/run")]

        payload = {
            "kind": "persona_refresh",
            "persona_id": persona_id,
            "login_steps": persona.login_steps,
            "headers": persona.auth_headers or {},
        }
        try:
            resp = requests.post(
                f"{engine_url}/persona-refresh",
                json=payload,
                timeout=30,
            )
            if resp.status_code >= 300:
                # Engine hasn't implemented the endpoint yet (Phase B scaffold);
                # leave a breadcrumb but don't fail loudly.
                print(f"[PersonaRefresh] engine returned {resp.status_code}: {resp.text[:200]}")
                return {"status": "engine_unavailable", "code": resp.status_code}
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            print(f"[PersonaRefresh] engine call failed: {exc}")
            return {"status": "engine_error", "error": str(exc)}

        new_state = data.get("storage_state")
        if new_state:
            persona.session_state = new_state
            persona.last_refreshed_at = datetime.utcnow()
            session.add(persona)
            session.commit()
            return {"status": "refreshed", "persona_id": persona_id}
        return {"status": "no_storage_state_returned"}
