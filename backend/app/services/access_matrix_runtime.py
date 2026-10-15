"""Dispatch-side helpers for identity-matrix runs and access-matrix cells.

Sync (Celery / run dispatcher) counterpart of `app/services/access_matrix.py`,
kept separate so `app/worker.py` can import it without pulling in pydantic row
models.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

# Headers that carry an identity. The anonymous column drops them from the
# suite's settings too: a suite-level `Authorization: Bearer …` IS a session,
# and an "anonymous" probe that still sends it would test nothing.
CREDENTIAL_HEADERS = frozenset({
    "authorization", "cookie", "x-api-key", "proxy-authorization", "x-auth-token",
    "x-access-token", "x-csrf-token", "x-xsrf-token",
})

RESULT_KEY = "access_check:result:{}"
# A worker result parked in Redis lives this long; the collector polls every
# few seconds, so this only matters when the collector is down.
RESULT_TTL_SECONDS = 3600


def _strip_headers(headers: Any) -> Dict[str, Any]:
    if not isinstance(headers, dict):
        return {}
    return {k: v for k, v in headers.items() if str(k).lower() not in CREDENTIAL_HEADERS}


def strip_identity(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Remove every identity from run settings, in place: stored session and
    credential headers, including per-domain header overrides."""
    settings.pop("storage_state", None)
    settings["headers"] = _strip_headers(settings.get("headers"))
    ds = settings.get("domain_settings")
    if isinstance(ds, dict):
        for dom, conf in ds.items():
            if isinstance(conf, dict) and "headers" in conf:
                conf["headers"] = _strip_headers(conf.get("headers"))
    return settings


def security_settings_for(project) -> Dict[str, Any]:
    from app.models import DEFAULT_SECURITY_SETTINGS
    merged = {**DEFAULT_SECURITY_SETTINGS.model_dump(), **(getattr(project, "security_settings", None) or {})}
    return merged


def security_allowlist_for(project_id: Optional[int], session) -> List[str]:
    """The project's security allowlist when security testing is enabled, else []."""
    if not project_id:
        return []
    from app.models import Project
    project = session.get(Project, project_id)
    if not project:
        return []
    sec = security_settings_for(project)
    if not sec.get("enabled"):
        return []
    return [d for d in (sec.get("allowed_domains") or []) if d]


def identity_for(persona) -> Dict[str, Any]:
    """{storage_state, headers} for a persona (session decrypted), or anonymous."""
    if persona is None:
        return {"anonymous": True, "headers": {}}
    from app.core.secrets import decrypt_json
    state = None
    if persona.session_state:
        try:
            state = decrypt_json(persona.session_state)
        except Exception as e:  # noqa: BLE001 — a rotated key degrades to no session
            print(f"[AccessMatrix] could not decrypt persona {persona.id} session: {e}")
    return {"anonymous": False, "storage_state": state,
            "headers": dict(persona.auth_headers or {})}


def build_cell_job(*, cell_id: int, row: Dict[str, Any], url: str,
                   identity: Dict[str, Any], owner: Optional[Dict[str, Any]],
                   allowed_domains: List[str], authorized: bool,
                   data_policy: Optional[Dict[str, Any]], matrix_run_id: int) -> Dict[str, Any]:
    """The worker job for one request/page cell. Pure so it can be unit-tested.

    The worker re-checks the allowlist and the attestation (defence in depth —
    a crafted stream entry should not be able to aim it elsewhere) and reports
    an observation only; the verdict is computed back here.
    """
    job_id = str(uuid.uuid4())
    return {
        "job_id": job_id,
        "job_type": "access_check",
        "run_id": 0,
        "cell_id": cell_id,
        "matrix_run_id": matrix_run_id,
        "result_key": RESULT_KEY.format(cell_id),
        "browser": "chromium",
        "target": {
            "kind": row.get("kind"),
            "method": (row.get("method") or "GET").upper(),
            "url": url,
            "body": row.get("body"),
            "headers": row.get("headers") or {},
            "selector": row.get("selector"),
        },
        "identity": identity,
        "owner": owner,
        "allowed_domains": list(allowed_domains),
        "authorized": bool(authorized),
        "data_policy": data_policy or {},
    }
